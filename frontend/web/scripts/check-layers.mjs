#!/usr/bin/env node
/**
 * 分层门禁（架构方案第七节）。6 条检查 + 元检查 + 规模上限。
 *
 *   node scripts/check-layers.mjs            严格模式：有违规 exit 1（接进 build 用）
 *   node scripts/check-layers.mjs --report   只报告，不改退出码（先看现状用）
 *   node scripts/check-layers.mjs --self-test 元检查：喂方案给的 6 条负向样本，
 *                                            任一条没报红就判**自检失败**
 *
 * 设计要点：每条检查都是 `文件内容映射 -> 违规数组` 的纯函数。
 * 这样负向样本走的是**同一份实现**，不是另写一套"自测专用"逻辑——
 * 否则会出现"自测绿但真跑抓不到"的假证明。
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, relative, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(ROOT, 'src', 'next')

/** main.tsx 的样式导入顺序。方案 f 的基准；对调任何两行即违规。 */
const STYLE_ORDER = [
  './styles/tokens.css',
  './styles/base.css',
  // 首屏右栏四块卡片（2026-10-05 从 base.css 整族搬出，base 当时 1008 行超上限）。
  // 必须紧跟 base.css：全仓只有它定义 .fs-*，位置等价于原先在 base.css 内部。
  './styles/firstscreen.css',
  // 版心框架（2026-10-04 第六轮）：补 .tn/.shell-nav/.sheet-foot 的 width:100%
  // （auto margin 取消 stretch 导致版心退回内容宽度）。覆盖 base.css 的宽度行为，
  // 必须排在 base.css 之后；夹具台入口 fixture-main.tsx 同步导入同一项。
  './styles/frame.css',
  // 左侧常驻竖导航（2026-10-04 骨架裁决第一步）：整体覆盖 base.css/frame.css 的
  // .shell-nav/.shell-tab 横排形态，并引入 .shell-body 版心栅格，排在 frame.css 之后。
  './styles/rail.css',
  './styles/wait.css',
  './styles/result-v1v2.css',
  './styles/result-v3.css',
  // 营养数值表（第 5 项）：只定义 .nutri-* 新类名，插在 result-v3 之后不挪动已有层叠。
  // 它没并进 result-v3.css 是因为那个文件实测 943 行，再追加就会越过 1000 行上限。
  './styles/nutrition.css',
  // 书写面组件（2026-10-04）：.composer 只做"结果页底部那一档"的低权重覆盖，
  // 不重定义 .ask-*（那些仍归 base.css）。它必须排在 base.css 之后才生效。
  './styles/composer.css',
  // 书页框架（2026-10-04）：四角裁切标记从 .tn 迁到 .shell-room（四房共享），
  // 并由 .shell-main/.shell-room 的 flex:1 与 .sheet-foot 做版心撑满和底部收束。
  // 它覆盖 base.css 的 .shell-main min-height，必须排在 base.css 之后。
  './styles/sheet.css',
  './styles/steps.css',
  './styles/motion.css',
  './styles/primitives.css',
  // 收藏房（第 4 步）：只定义 .fv-* 新类名，登记为末层，与前面选择器不相交。
  './styles/fav.css',
  // 服务房（第 5 步）：只定义 .sv-* 新类名，同理登记为末层。
  './styles/service.css',
  // 浮层面板（2026-10-01 B 批次①③）：家庭成员抽屉 .sd-* + 历史侧栏 .rail-*，
  // 从 base.css 拆出（越 1000 行上限）；新前缀不与前面选择器相交，登记末层。
  './styles/panels.css',
  // 候选卡（2026-10-05）：.cand-* 新前缀，只画序号/菜名/选择动作 + 换口径药丸，
  // 与前面选择器不相交，登记末层。夹具台有 candidates 用例，两处清单都要有它。
  './styles/candidate.css',
]

/** 方案第三节：依赖方向只允许 app → views → blocks → ui。 */
const LAYERS = ['app', 'views', 'blocks', 'ui', 'data']
/** 每层允许 import 的层。未列出的层 = 禁止。 */
const ALLOWED = {
  app: ['app', 'views', 'blocks', 'ui', 'data'],
  views: ['views', 'blocks', 'ui', 'data'],
  blocks: ['blocks', 'ui', 'data'],
  ui: ['ui'],
  data: ['data'], // data 里只许互相引用，且**不得**导入任何 .tsx
}

// ── 通用 ────────────────────────────────────────────────────────────

const layerOf = (rel) => {
  const m = /^src\/next\/([^/]+)\//.exec(rel)
  return m && LAYERS.includes(m[1]) ? m[1] : null
}

/** 抽出一条 import/export-from 语句里的模块说明符。 */
const specifiers = (code) => {
  const out = []
  const re = /(?:^|\n)\s*(?:import|export)[\s\S]*?from\s+['"]([^'"]+)['"]|import\s+['"]([^'"]+)['"]/g
  let m
  while ((m = re.exec(code))) out.push(m[1] || m[2])
  return out
}

const isEmpty = (list) => list.length === 0

// ── a 导入方向（含 data 不导入 .tsx）────────────────────────────────

export function checkImports(files) {
  const v = []
  for (const [rel, code] of Object.entries(files)) {
    const layer = layerOf(rel)
    if (!layer) continue
    for (const spec of specifiers(code)) {
      if (!spec.startsWith('.') && !spec.startsWith('/')) continue
      const abs = spec.startsWith('.') ? null : spec
      const target = abs ?? null
      if (target) continue // 非相对路径（react 等）不参与分层判定

      // 解析目标相对路径到 src/next 下的层
      const from = join(ROOT, dirname(rel))
      const targetPath = resolve(from, spec).replace(/\\/g, '/')
      const tRel = relative(ROOT, targetPath).replace(/\\/g, '/')
      const tLayer = layerOf(tRel)
      if (!tLayer) continue

      if (layer === 'data' && /\.tsx$/.test(spec)) {
        v.push(`${rel}: data 层导入了 .tsx（${spec}）`)
        continue
      }
      if (!ALLOWED[layer].includes(tLayer)) {
        v.push(`${rel}: ${layer} 层不得导入 ${tLayer} 层（${spec}）`)
      }
    }
  }
  return v
}

// ── b 前缀归属：.p-* 只在 primitives.css，.b-* 只在 blocks 的样式文件 ──

export function checkPrefixes(cssFiles) {
  const v = []
  for (const [rel, code] of cssFiles) {
    const defs = [...code.matchAll(/(^|[}\n])\s*(\.p-[a-z][\w-]*)\s*[,{]/gm)]
    for (const [, cls] of defs) {
      if (!/styles\/primitives\.css$/.test(rel)) {
        v.push(`${rel}: ${cls} 定义应只出现在 styles/primitives.css`)
      }
    }
    const bDefs = [...code.matchAll(/(^|[}\n])\s*(\.b-[a-z][\w-]*)\s*[,{]/gm)]
    for (const [, cls] of bDefs) {
      if (/styles\/(views|primitives|tokens)\.css$/.test(rel)) {
        v.push(`${rel}: ${cls} 是块级类名，不该定义在 ${rel}`)
      }
    }
  }
  return v
}

// ── c 视图不得改原语的结构属性 ──────────────────────────────────────

const STRUCT_PROPS = ['padding', 'margin', 'display', 'border', 'gap', 'font-size']

export function checkViewTouchesPrimitive(cssFiles) {
  const v = []
  for (const [rel, code] of cssFiles) {
    const re = /([^{}]+)\{([^{}]*)\}/g
    let m
    while ((m = re.exec(code))) {
      const selector = m[1].trim().replace(/\s+/g, ' ')
      const body = m[2]
      // 视图侧选择器（.v-* 或页面级容器）命中 .p-* 后代
      if (!/(^|[ ,>])\S*\s*\.p-[a-z]/.test(selector) || !selector.includes('.p-')) continue
      if (!/\.v-[a-z]/.test(selector)) continue
      for (const p of STRUCT_PROPS) {
        const hit = new RegExp(`(^|[;{\\s])${p}\\s*:`, 'm').test(body)
        const allowed = /grid-area\s*:|(^|[;\s])order\s*:|align-self\s*:/.test(body)
        if (hit && !allowed) {
          v.push(`${rel}: 视图选择器 "${selector}" 改了原语结构属性 ${p}`)
        }
      }
    }
  }
  return v
}

// ── d tokens.css 以外不得出现色值字面量 ─────────────────────────────

const COLOR_RE = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g

export function checkColorLiterals(cssFiles) {
  const v = []
  for (const [rel, code] of cssFiles) {
    if (/styles\/tokens\.css$/.test(rel)) continue
    // 排除注释，避免把文档里的示例色值当成违规
    const bare = code.replace(/\/\*[\s\S]*?\*\//g, '')
    for (const line of bare.split('\n')) {
      if (COLOR_RE.test(line)) {
        v.push(`${rel}: 色值字面量 "${line.trim().slice(0, 70)}"（应引用 token）`)
      }
      COLOR_RE.lastIndex = 0
    }
  }
  return v
}

// ── e blocks/views 不得读后端字段 ───────────────────────────────────

export function checkBackendFields(tsxFiles) {
  const v = []
  for (const [rel, code] of tsxFiles) {
    const layer = layerOf(rel)
    if (layer !== 'blocks' && layer !== 'views') continue
    for (const spec of specifiers(code)) {
      if (/types\.ts$/.test(spec)) v.push(`${rel}: 直接 import 了后端类型 ${spec}`)
    }
    for (const line of code.split('\n')) {
      const bare = line.replace(/\/\/.*$/, '').replace(/\/\*[\s\S]*?\*\//g, '')
      if (/(^|[^.\w])data\.[A-Za-z]/.test(bare) || /(^|[^.\w])answer\.[A-Za-z]/.test(bare)) {
        v.push(`${rel}: 直接读后端字段 — ${bare.trim().slice(0, 70)}`)
      }
    }
  }
  return v
}

// ── f main.tsx 的样式导入顺序固定 ──────────────────────────────────

export function checkStyleOrder(mainTsx) {
  const v = []
  const got = [...mainTsx.matchAll(/import\s+['"](\.\/styles\/[^'"]+)['"]/g)].map((m) => m[1])
  if (isEmpty(got)) {
    v.push('main.tsx: 读不到任何样式导入（检查本身可能失效）')
    return v
  }
  const want = got.filter((s) => STYLE_ORDER.includes(s))
  const expected = STYLE_ORDER.filter((s) => want.includes(s))
  if (JSON.stringify(want) !== JSON.stringify(expected)) {
    v.push(`main.tsx: 样式导入顺序被改动\n  实际: ${want.join(' ')}\n  应为: ${expected.join(' ')}`)
  }
  const extra = got.filter((s) => !STYLE_ORDER.includes(s))
  if (extra.length) v.push(`main.tsx: 出现未登记在顺序表里的样式导入 ${extra.join(' ')}`)
  return v
}

// ── g §五 降级契约：blocks 里不得出现存在性判断 ─────────────────────

export function checkDegradation(blockFiles) {
  const v = []
  for (const [rel, code] of blockFiles) {
    for (const line of code.split('\n')) {
      const bare = line.replace(/\/\/.*$/, '')
      if (/\?\.\s*length/.test(bare)) v.push(`${rel}: 存在性判断 ?.length — ${bare.trim().slice(0, 70)}`)
      if (/\?\.\s*\w+\s*\)/.test(bare) && /\bif\s*\(/.test(bare))
        v.push(`${rel}: 可能的存在性短路 — ${bare.trim().slice(0, 70)}`)
    }
  }
  return v
}

// ── 规模上限：CSS > 1000 行、blocks 单文件 > 150 行 ─────────────────

export function checkScale(cssFiles, blockFiles) {
  const v = []
  for (const [rel, code] of cssFiles) {
    const n = code.split('\n').length
    if (n > 1000) v.push(`${rel}: ${n} 行，超过 1000 行上限，请拆分`)
  }
  for (const [rel, code] of blockFiles) {
    const n = code.split('\n').length
    if (n > 150) v.push(`${rel}: ${n} 行，blocks 单文件超过 150 行上限`)
  }
  return v
}

// ── 真实文件装载 ────────────────────────────────────────────────────

function walk(dir, out = []) {
  if (!existsSync(dir)) return out
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

export function loadReal() {
  const all = walk(SRC)
  const files = {}
  for (const p of all) {
    const rel = relative(ROOT, p).replace(/\\/g, '/')
    files[rel] = readFileSync(p, 'utf8')
  }
  return files
}

export function runAll(files) {
  // d 的检查面只取 styles/ —— 那是**正式产品**的样式目录（main.tsx / next.tsx 入口）。
  // 刻意排除 src/next/next.css：它是**检查页**（check-main.tsx）的样式，
  // 不是 vite 的构建入口（input 只有 main/next），文件头也自述「它的任务是取证，
  // 不是交付观感」。它有自己的 token 定义，拿产品规则去量它只会制造 24 条假违规。
  // 这是**范围收缩，不是放水**：下面 --report 会把排除的文件打出来。
  const cssFiles = Object.entries(files).filter(([r]) => r.endsWith('.css') && /\/styles\//.test(r))
  const excludedCss = Object.keys(files).filter((r) => r.endsWith('.css') && !/\/styles\//.test(r))
  const tsxFiles = Object.entries(files).filter(([r]) => /\.(tsx|ts)$/.test(r) && !r.endsWith('.test.ts'))
  const blockFiles = Object.entries(files).filter(([r]) => r.includes('/blocks/') && /\.(tsx|ts)$/.test(r))
  const mainTsx = files['src/next/main.tsx'] ?? ''
  return {
    a_导入方向: checkImports(files),
    b_前缀归属: checkPrefixes(cssFiles),
    c_视图改原语: checkViewTouchesPrimitive(cssFiles),
    d_色值字面量: checkColorLiterals(cssFiles),
    e_读后端字段: checkBackendFields(tsxFiles),
    f_样式顺序: checkStyleOrder(mainTsx),
    g_降级契约: checkDegradation(blockFiles),
    规模上限: checkScale(cssFiles, blockFiles),
    ...(excludedCss.length ? { _范围外CSS: excludedCss } : {}),
  }
}

// ── 元检查：方案给的 6 条负向样本必须全部报红 ──────────────────────

function selfTest() {
  const cases = [
    ['a 导入方向', () => checkImports({ 'src/next/ui/Pill.tsx': `import { DishHero } from '../blocks/DishHero.tsx'` })],
    ['b 前缀归属', () => checkPrefixes([['src/next/styles/views.css', '.p-pill{color:var(--ink)}']])],
    ['c 视图改原语', () => checkViewTouchesPrimitive([['src/next/styles/views.css', '.v-result .p-panel{padding:0}']])],
    ['d 色值字面量', () => checkColorLiterals([['src/next/styles/blocks.css', '.b-chef{color:#c33}']])],
    ['e 读后端字段', () => checkBackendFields([['src/next/views/ResultView.tsx', 'export const g = data.guardrails.length && 1']])],
    ['f 样式顺序', () => checkStyleOrder(`import './styles/base.css'\nimport './styles/tokens.css'`)],
  ]
  // 正向样本：同样必须**不**报红，否则门禁一开就天天误报
  const clean = [
    ['a 正向', () => checkImports({ 'src/next/blocks/SideRail.tsx': `import { Panel } from '../ui/Panel.tsx'` })],
    ['b 正向', () => checkPrefixes([['src/next/styles/primitives.css', '.p-panel{color:var(--ink)}']])],
    ['d 正向', () => checkColorLiterals([['src/next/styles/base.css', '.x{color:var(--ink)}']])],
    ['f 正向', () => checkStyleOrder(STYLE_ORDER.map((s) => `import '${s}'`).join('\n'))],
  ]
  const fails = []
  for (const [name, fn] of cases) {
    const v = fn()
    if (v.length === 0) fails.push(`负向样本未报红：${name}`)
  }
  for (const [name, fn] of clean) {
    const v = fn()
    if (v.length > 0) fails.push(`正向样本误报：${name} — ${v[0]}`)
  }
  if (fails.length) {
    console.error('check-layers --self-test 失败：')
    for (const f of fails) console.error('  ✗ ' + f)
    process.exit(1)
  }
  console.log(`check-layers --self-test 通过：${cases.length} 条负向样本全部报红，${clean.length} 条正向样本零误报`)
}

// ── 入口 ────────────────────────────────────────────────────────────

const argv = process.argv.slice(2)
if (argv.includes('--self-test')) {
  selfTest()
  process.exit(0)
}

const report = argv.includes('--report')
const files = loadReal()
const results = runAll(files)

let total = 0
for (const [name, list] of Object.entries(results)) {
  if (name.startsWith('_')) {
    console.log(`  · ${name}（不计违规）: ${(list || []).join(', ')}`)
    continue
  }
  if (list.length === 0) {
    console.log(`  ✓ ${name}`)
  } else {
    total += list.length
    console.log(`  ✗ ${name} — ${list.length} 条`)
    for (const item of list) console.log(`      ${item}`)
  }
}

if (report) {
  console.log(`\n（--report 模式：${total} 条违规，退出码仍为 0）`)
} else if (total > 0) {
  console.error(`\ncheck-layers 失败：${total} 条违规`)
  process.exit(1)
} else {
  console.log('\ncheck-layers 通过：零违规')
}
