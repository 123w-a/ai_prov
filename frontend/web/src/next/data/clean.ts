/**
 * 正文清洗。
 *
 * 从 `Result.tsx` 迁出（架构方案第八节第 3 步）。**纯函数、不含 React**：
 * 这一层只回答"这段文字能不能印、印出来是什么"，不做任何渲染决定。
 *
 * 为什么值得单独一个文件：这些规则以前长在 795 行的组件里，只能靠读组件来验证，
 * 而它们的 bug 全都是**看真实数据才发现的**（Python 的 None 被序列化成字面量、
 * 用户输入被注入 GPS 坐标前缀、opening 里粘进结构模型自己的 JSON 残骸）。
 * 迁出来之后可以直接 `node --test` 喂样本。
 *
 * ⚠️ 这里全部是**迁移**，不是重写：函数体逐字保留（含每个正则）。
 * 判据是「迁移前后渲染结果完全一致」——正则改一个字符，页面就可能变，而基线会当场抓住。
 */

/** opening 里偶尔会粘进结构模型自己的 JSON 残骸（实测 1/23）。在第一个结构标记处截断。 */
const EMBEDDED_JSON = /\}\s*\{\s*"opening"\s*:/
const ESCAPED_MD = /\\n\\n#|\\n\d+\.\s|\\n[-*]\s/
const TAIL_RESIDUE = /"\s*\}\s*\]\s*,?\s*"(?:guardrails|health_lights|sources|recipes|dish_matrix|member_adjustments)"\s*:/

export function cleanOpening(source?: string, max = 8000): string {
  let text = (source ?? '').trim()
  for (const marker of [EMBEDDED_JSON, ESCAPED_MD, TAIL_RESIDUE]) {
    const at = text.search(marker)
    if (at > 0) text = text.slice(0, at).trimEnd()
  }
  if (text.length > max) text = `${text.slice(0, max).trimEnd()}…`
  return text
}

/** 只留"实词"，用来判断两段文字是不是同一篇。 */
function skeleton(source: string): string {
  return source
    .replace(/[#*>\-—\s\d.、，。：；!?！？（）()"'“”「」]/g, '')
    .slice(0, 140)
}

/** body 和 opening 是同源还是两份不同内容。 */
export function sameSource(a: string, b: string): boolean {
  const x = skeleton(a)
  const y = skeleton(b)
  if (x.length < 30 || y.length < 30) return false
  return y.includes(x.slice(0, 40)) || x.includes(y.slice(0, 40))
}

/**
 * 前端会往用户输入前面注入机器生成的上下文块。实测 37 条 user_text 里 17 条带坐标前缀：
 *     [当前位置：112.383184,28.542730]\r\n想去外面吃炸鸡。
 *     [实时状态：昨晚没睡好]\r\n[当前位置：112.383343,28.542442]\r\n早上了，想吃点便宜又管饱的东西…
 * 这些不是用户说过的话。回声行的作用是"把你自己的原话引回来"，
 * 把一串 GPS 坐标当成用户的原话印出来是错的（而且用户从没打过那串数字）。
 *
 * 只剥"已知机器键 + 方括号"这一种形态，不用通用规则去猜，
 * 免得把用户自己写的方括号内容也吃掉。
 */
const MACHINE_PREFIX =
  /^\s*\[(?:当前位置|实时状态|定位|坐标|经纬度|当前时间|现在时间)\s*[:：][^\]]{0,80}\]/

function stripMachinePrefix(source: string): string {
  let text = source
  for (let i = 0; i < 4; i += 1) {
    const next = text.replace(MACHINE_PREFIX, '')
    if (next === text) break
    text = next
  }
  return text
}

/** 回声行会把用户原话原样引出来，太长就截断——它不是正文。 */
export function echoRequest(source: string, max = 52): string {
  const text = stripMachinePrefix(source).trim().replace(/\s+/g, ' ')
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max)}…` : text
}

/**
 * 后端有时会把 Python 的 None 直接序列化成字符串 "None" 写进 image_note。
 * 实测：顶层 image_note 22/23 有真实内容，但**菜品级 20/23 是空或字面量 "None"**。
 * 不做这个判定，页面上就会印出一个字面量 None。
 */
export function usableNote(value?: string | null): string {
  const text = (value ?? '').trim()
  if (!text) return ''
  const low = text.toLowerCase()
  if (low === 'none' || low === 'null' || low === 'nil' || low === 'undefined') return ''
  return text
}

/**
 * 护栏状态词只做翻译，不发明。实测 guardrails.status 的取值全是 "pass"（共 3 条）。
 *
 * `pass` 返回空串是刻意的：它的 reason 本身就是「已符合高血压膳食原则」，
 * 再挂一个「已符合」就是同一句话说两遍（`高血压 已符合 · 已符合高血压膳食原则`）。
 * 未知取值原样透出——绝不把它猜成某个已知档位。
 */
const GUARD_WORD: Record<string, string> = {
  pass: '',
  warn: '需注意',
  warning: '需注意',
  caution: '需注意',
  fail: '不符合',
  blocked: '已避开',
}

export function guardText(status?: string, reason?: string): string {
  const raw = (status ?? '').trim()
  const key = raw.toLowerCase()
  const word = key in GUARD_WORD ? GUARD_WORD[key] : raw
  return [word, (reason ?? '').trim()].filter(Boolean).join(' · ')
}

/**
 * 剥掉调整文案开头的菜名前缀。
 *
 * 实测同一条答案里 member_adjustments 与 dish_matrix 讲的是同一件事：
 *   member_adjustments = "我：「糖色香菇红烧肉（全家可吃版）」主食减半、蔬菜加量…"
 *   dish_matrix[我]     = { verdict:"需调整", reason:"主食减半、蔬菜加量…" }
 * 合成正文时取调整文案，它开头会带一遍菜名，而菜名已经在标题位了。
 */
export function stripDishPrefix(body: string, dishes: string[]): string {
  let text = body.trim()
  // 开头的「菜名」整段剥掉
  const quoted = /^[「『][^」』]{1,40}[」』]/.exec(text)
  if (quoted) text = text.slice(quoted[0].length).trim()
  // 直接以某个菜名开头的，也剥掉
  for (const d of dishes) {
    const name = (d || '').trim()
    if (name && text.startsWith(name)) text = text.slice(name.length).trim()
  }
  return text
}

/**
 * 来源的显示名。
 *
 * 真实 source 就是文件名（实测 66 条：63 条带扩展名，长度中位 21 字、最长 27 字），
 * 一条「4_备孕孕期妇女膳食指南解读_杨年红2022.pdf」塞进 278px 的右栏要占三行。
 * 这里只做**能可靠做**的两件事：去掉前导序号、去掉扩展名，下划线换成间隔号。
 * **不再往下截**——硬砍成「备孕孕期膳食指南」要靠猜，会把"是哪一份指南"这个
 * 关键信息砍掉。全名与原文引用仍留在正文末尾的「本轮依据」里可达。
 */
export function srcTitle(raw: string): string {
  const s = raw
    .replace(/^\d+[_.、]\s*/, '')
    .replace(/\.(pdf|docx?|txt|md)$/i, '')
    .replace(/_/g, '·')
    .trim()
  return s || raw
}

/**
 * 从**确定性出处**里取出页码部分，右栏单独一行小字显示。
 *
 * guardrails 的 source 与上面那条（文件名）不是一个形状：它是
 * 「指南名（版本）页码」，形如
 *   · 成人高血压食养指南（2023年版）p6-7
 *   · 成人糖尿病食养指南（2023年版）p7,p11
 *   · 中国备孕和孕期妇女膳食指南（2022）解读（杨年红）p4；中国哺乳期妇女膳食指南（2022）解读（杨振宇）p5
 * 前两种还好，第三种有 50 个字，直接塞进 208px 的栏里要折三四行。
 * 而"能拿去核对"的关键恰好是**页码**——用户翻开那份指南直接跳到 p4 就行，
 * 指南全名反而是次要的。所以把页码单独提出来显示，名字走 CSS 单行截断、
 * 全名挂在 title 上（悬停可见）。
 *
 * 只认 p/p 加数字这种最稳的形态，取不到就返回空串（调用方整段不渲染），
 * 绝不拿名字去猜页码。
 */
export function srcPage(raw: string): string {
  const hits = String(raw ?? '').match(/p\s*\d+(?:\s*[-–,，]\s*\d+)*/gi) ?? []
  const seen = new Set<string>()
  const out: string[] = []
  for (const h of hits) {
    const t = h.replace(/\s+/g, '').toLowerCase()
    if (seen.has(t)) continue
    seen.add(t)
    out.push(t)
  }
  return out.join(' · ')
}
