/**
 * 视图模型：**唯一**接触后端原始响应类型的地方（架构方案第三节）。
 *
 * 目的只有一个：把"这个字段在不在、空不空、坏没坏"的判断从 JSX 里彻底挪出来。
 * 以前这些判断散在 795 行组件的 25 个 `&&` 里，每个块各写各的，于是同类问题反复出现
 * （后端把 Python 的 None 序列化成字面量 "None"、空右栏渲染成一个明晃晃的圆角空框）。
 *
 * ── 降级契约（方案第五节）────────────────────────────────────────────────
 *
 *   内部判**五态**：present / absent / empty / invalid / error
 *   对外只输出**三态**：show / hide / placeholder
 *
 * 为什么不让五态进 JSX：那样每个块都要写五个分支，而 invalid 与 absent 在界面上
 * 本来没有区别，分支迟早各改各的——那就是崩法。为什么又不只用三态：只用三态会丢掉原因，
 * 测试就分不清"数据为空"和"数据坏了"，所以 state 保留在返回值上给测试看，JSX 不读它。
 *
 * `error` 是**请求级**的，由状态机处理，不按字段处理——所以这里不产出 error。
 *
 * ── 本文件是**迁移**不是重写 ────────────────────────────────────────────────
 *
 * 每一条规则都照抄 Result.tsx 里原有的判断，一个字没改（包括"空右栏不渲染"这种
 * 因为踩过坑才补上的外层条件）。判据是「迁移前后渲染结果完全一致」，由基线逐元素比对。
 * 阅读顺序的调整（结论 → 关键调整 → 步骤 → 需留意 → 备选来源）不在这里做，留到第 5 步。
 */

import type {
  ChefAnswer,
  DishMatrixItem,
  GuardrailItem,
  HealthLight,
  Recipe,
  Seasoning,
  SourceRef,
} from '../../types.ts'
import { cleanOpening, echoRequest, stripDishPrefix } from './clean.ts'
import { formatSpoken } from './model.ts'

/* 后端类型只从这里转出。
   blocks / views 不得直接 import ../../types.ts —— check-layers 的 e 条会拦。
   它们一律从 viewModel 拿，否则块就开始直接认后端形状、跳过降级了。 */
export type { Recipe }

/* ── 契约类型 ─────────────────────────────────────────────────────────── */

export type FieldState = 'present' | 'absent' | 'empty' | 'invalid' | 'error'

export type Display<T> =
  | { display: 'show'; data: T; state: FieldState; reason?: string }
  | { display: 'hide'; state: FieldState; reason?: string }
  | { display: 'placeholder'; text: string; state: FieldState; reason?: string }

/** 三态在 JSX 里的读法。state/reason 是给测试看的，JSX 不读。 */
export function isShown<T>(d: Display<T>): d is { display: 'show'; data: T; state: FieldState; reason?: string } {
  return d.display === 'show'
}

export const show = <T,>(data: T, state: FieldState = 'present', reason?: string): Display<T> => ({
  display: 'show',
  data,
  state,
  reason,
})

export const hide = <T,>(state: FieldState, reason?: string): Display<T> => ({ display: 'hide', state, reason })

export const placeholder = <T,>(text: string, state: FieldState, reason?: string): Display<T> => ({
  display: 'placeholder',
  text,
  state,
  reason,
})

/** 判五态。只回答"这是什么状态"，不回答"该怎么显示"——显示规则上面那三个函数负责。 */
export function classify(value: unknown): FieldState {
  if (value === undefined || value === null) return 'absent'
  if (typeof value === 'string') return value.trim() === '' ? 'empty' : 'present'
  if (Array.isArray(value)) return value.length === 0 ? 'empty' : 'present'
  if (typeof value === 'number') return Number.isFinite(value) ? 'present' : 'invalid'
  if (typeof value === 'boolean') return 'present'
  return 'present'
}

/**
 * 非空数组才 show。空数组和缺字段在界面上没有区别，但**原因要能区分**，
 * 所以 state 一路带下去：空数组是 empty，缺字段是 absent。
 */
function list<T>(value: T[] | undefined | null, reason?: string): Display<T[]> {
  const items = value ?? []
  const state = classify(value)
  if (items.length === 0) return hide<T[]>(state, reason ?? (state === 'absent' ? '后端未给该字段' : '后端给了空数组'))
  return show(items, state)
}

/** 数字字段：有值才 show。NaN 也是 number，但它是**坏值**不是有值——
 *  两者都不显示，而 state 要分得开，这就是五态存在的全部理由。 */
function value(n: number | undefined | null, reason: string): Display<number> {
  const state = classify(n)
  return state === 'present' ? show(n as number) : hide<number>(state, reason)
}

/** 非空字符串才 show（先 trim，全空白等同于空）。 */
function text(value: string | undefined | null, reason?: string): Display<string> {
  const raw = value ?? ''
  const state = classify(value)
  const trimmed = raw.trim()
  if (!trimmed) return hide<string>(state, reason ?? (state === 'absent' ? '后端未给该字段' : '字段是空白串'))
  return show(trimmed, state)
}

/* ── 视图模型 ─────────────────────────────────────────────────────────── */

/** 灯级 → 语义色名。方案第四节：这个映射**只准写在这里**。 */
export type Tone = 'ok' | 'warn' | 'risk'

const LEVEL_TONE: Record<HealthLight['level'], Tone> = { green: 'ok', yellow: 'warn', red: 'risk' }

export interface LightVM {
  label: string
  /** 后端原样取值，当前 CSS 的类名还在用它（.light.is-red）。第 5 步换成 data-tone。 */
  level: HealthLight['level']
  tone: Tone
  reason: Display<string>
}

export interface GuardVM {
  condition: string
  /** guardText 的产物：已经翻译成中文，没有可显示内容时是 hide。 */
  text: Display<string>
}

export interface MemberLineVM {
  key: string
  verdict: string
  dish: string
  body: string
}

export interface MembersVM {
  /** 有数据的人，顺序按 dish_matrix 出现顺序。 */
  order: string[]
  lines: Record<string, MemberLineVM[]>
  /** 没归到任何人的调整文案。 */
  orphans: string[]
  /** 矩阵里出现超过一个菜名时才在每行印菜名（实测单菜答案里那是纯噪声）。 */
  showDish: boolean
}

export interface ResultVM {
  form: 'structured' | 'prose'
  isStructured: boolean

  opening: Display<string>
  echo: Display<string>

  lead: Recipe | undefined
  /** 顶层配图说明。菜品级那个实测 20/23 是空或字面量 "None"（usableNote 负责判）。 */
  imageNote: string | undefined
  rest: Display<Recipe[]>
  steps: Display<string[]>
  seasonings: Display<Seasoning[]>

  difficulty: Display<number>
  nutrition: Display<number>
  intro: Display<string>
  chefTip: Display<string>

  reds: LightVM[]
  yellows: LightVM[]
  greens: LightVM[]
  /** 右栏只用**一张**表，按严重度排。分两张会让同一条目在同一栏出现两遍
   *  （钠既是"黄警告"又是"营养行"），一栏之内自相重复——截图已复现。 */
  ranked: LightVM[]

  guardrails: GuardVM[]
  sources: SourceRef[]

  /** 常驻右栏整块是否渲染。2026-09-26：右栏从发丝竖线改成填充面板后，
   *  空栏会渲染成明晃晃的空圆角框（thin 形态实测如此），所以条件必须提到外层 aside 上。 */
  rail: Display<null>
  /** 右栏里「这顿要注意的」那一块（护栏 + 灯）。 */
  notice: Display<null>

  members: Display<MembersVM>

  /** 用时。正常结果来自状态机（不是后端响应），永远有值；
   *  **收藏恢复态传 null ⇒ 空串 ⇒ 首屏不渲染「用时」这一格**——
   *  收藏没存当时的真实用时，写 0 秒比不展示更糟。格式化归这里，视图不自己调 formatSpoken。 */
  elapsed: string

  /** 页底真实过程那一行的账目。 */
  process: { searches: number; sources: number; chars: number }
}

function toLight(l: HealthLight): LightVM {
  return {
    label: l.label,
    level: l.level,
    tone: LEVEL_TONE[l.level] ?? 'warn',
    reason: text(l.reason, '这条灯没有理由'),
  }
}

/** 安全护栏。status 的翻译已在 clean.guardText 里做过（未知取值原样透出）。 */
function toGuard(g: GuardrailItem, guardText: (status?: string, reason?: string) => string): GuardVM {
  const texted = guardText(g.status, g.reason)
  return {
    condition: g.condition,
    text: texted ? show(texted) : hide<string>('empty', '状态词与理由都为空（pass 且理由自述时就是这种）'),
  }
}

/**
 * 家人两列的数据整形。**只整形，不渲染**（渲染仍在 Result.tsx，第 5 步再搬）。
 *
 * 两条实测规则（看真实数据才发现的，注释保留原样）：
 *  1. 同一条答案里 member_adjustments 与 dish_matrix 讲的是同一件事，两处都印会重复一遍。
 *     合成方式：verdict 当引子，正文取调整文案、取不到才退回 matrix.reason；调整文案
 *     开头的菜名前缀要剥掉。
 *  2. 单菜答案里不重复印菜名——矩阵只有 1 个不同菜名时它是整页标题，每行再印是纯噪声。
 */
export function buildMembersVM(rows: DishMatrixItem[], notes: string[]): MembersVM | null {
  const dishes = [...new Set(rows.map((r) => (r.dish || '').trim()).filter(Boolean))]
  const showDish = dishes.length > 1

  const order: string[] = []
  const verdict = new Map<string, string>()
  const reason = new Map<string, string[]>()
  for (const r of rows) {
    const name = (r.member || '').trim()
    if (!name) continue
    if (!order.includes(name)) order.push(name)
    if (!verdict.get(name) && r.verdict) verdict.set(name, r.verdict)
    if (r.reason) {
      if (!reason.has(name)) reason.set(name, [])
      const list = reason.get(name)!
      if (!list.includes(r.reason)) list.push(r.reason)
    }
  }

  // member_adjustments 是 "名字：说明" 形态的纯字符串。
  const adjBody = new Map<string, string>()
  const orphans: string[] = []
  for (const raw of notes) {
    const rawText = (raw || '').trim()
    if (!rawText) continue
    const at = rawText.search(/[:：]/)
    const name = at > 0 ? rawText.slice(0, at).trim() : ''
    const body = stripDishPrefix(at > 0 ? rawText.slice(at + 1) : rawText, dishes)
    if (!body) continue
    if (name && (order.includes(name) || rows.some((r) => (r.member || '').trim() === name))) {
      if (!order.includes(name)) order.push(name)
      if (!adjBody.has(name)) adjBody.set(name, body)
    } else {
      orphans.push(rawText)
    }
  }

  if (order.length === 0 && orphans.length === 0) return null

  const lines: Record<string, MemberLineVM[]> = {}
  for (const name of order) {
    const mine = rows.filter((r) => (r.member || '').trim() === name)
    if (dishes.length > 1) {
      // 多菜（观测中未出现，但 schema 允许）时按"每人×每菜"各占一行并带菜名。
      lines[name] = mine.map((r, i) => ({
        key: `${name}-${i}`,
        verdict: r.verdict || '',
        dish: showDish ? (r.dish || '') : '',
        body: (r.reason || '').trim(),
      }))
    } else {
      lines[name] = [
        {
          key: name,
          verdict: verdict.get(name) || '',
          dish: '',
          body: adjBody.get(name) || (reason.get(name) ?? []).join(' '),
        },
      ]
    }
  }

  return { order, lines, orphans, showDish }
}

/** 结构化答案的视图模型。 */
export function buildResultVM(
  answer: ChefAnswer | undefined,
  /**
   * 结构化路径下正文只按 opening 计字（照抄原实现：`isStructured ? 0 : run.body.length`），
   * 所以这里刻意不读它——散文路径的字数在 buildProseVM 里算。
   */
  _body: string,
  request: string,
  /** null = 收藏恢复态，没有真实用时（见 ResultVM.elapsed 的说明）。 */
  elapsed: number | null,
  guardTextFn: (status?: string, reason?: string) => string,
): ResultVM {
  const a = answer
  const recipes = a?.recipes ?? []
  const lights = a?.health_lights ?? []
  const rows = a?.dish_matrix ?? []
  const adjustments = a?.member_adjustments ?? []
  const guardrailsRaw = a?.guardrails ?? []
  const sources = a?.sources ?? []

  const opening = cleanOpening(a?.opening)
  const lead = recipes[0]
  const echo = echoRequest(request)

  const lightVMs = lights.map(toLight)
  const reds = lightVMs.filter((l) => l.level === 'red')
  const yellows = lightVMs.filter((l) => l.level === 'yellow')
  const greens = lightVMs.filter((l) => l.level === 'green')
  const ranked = [...reds, ...yellows, ...greens]
  const guardrails = guardrailsRaw.map((g) => toGuard(g, guardTextFn))

  const members = buildMembersVM(rows, adjustments)

  return {
    form: 'structured',
    isStructured: true,

    // opening 的"没给"和"给了但是空"要分得开，所以先看原始字段再看清洗结果。
    opening: a?.opening === undefined || a?.opening === null
      ? hide<string>('absent', '后端未给 opening')
      : text(opening, 'opening 清洗后为空'),
    echo: text(echo, '没有可回声的用户原话'),

    lead,
    imageNote: a?.image_note,
    rest: list(recipes.slice(1), '只有一道菜'),
    // 传**原始值**（不预先 ?? []），否则"后端没给"会被压成"给了空数组"，五态就白判了。
    steps: list(lead?.steps, '这道菜没有步骤'),
    seasonings: list(lead?.seasonings, '这道菜没有调料'),

    difficulty: value(lead?.difficulty, '没有难度字段'),
    nutrition: value(lead?.nutrition, '没有营养字段'),
    intro: text(lead?.intro, '这道菜没有一句话介绍'),
    chefTip: text(a?.chef_tip, '这轮没有给重点提示'),

    reds,
    yellows,
    greens,
    ranked,

    guardrails,
    sources,

    // 外层条件照抄 Result.tsx 原文（含 2026-09-26 那次的修正）。
    rail: ranked.length > 0 || guardrails.length > 0 || sources.length > 0
      ? show(null)
      : hide<null>('empty', '没有灯、没有护栏、没有依据——空栏会渲染成空圆角框，所以整块不渲染'),
    notice: ranked.length > 0 || guardrails.length > 0
      ? show(null)
      : hide<null>('empty', '既没有营养灯也没有护栏'),

    members: members ? show(members) : hide<MembersVM>('empty', '没有 dish_matrix 也没有 member_adjustments'),

    elapsed: elapsed === null ? '' : formatSpoken(elapsed),

    process: {
      searches: 0, // 由调用方按 run.events 填，这里不碰运行状态
      sources: sources.length,
      chars: opening.length,
    },
  }
}

export interface ProseVM {
  empty: Display<null>
  heading: Display<string>
  echo: Display<string>
  text: string
  /** 页底账目里的正文字数。用**未 trim** 的原始长度，照抄原实现。 */
  chars: number
}

/**
 * 纯散文结局的数据整形。
 *
 * 首行如果是 Markdown 标题，它本来就承担着标题的角色，把它提到标题位、
 * 不在正文里重复。正文全空时给的是**明确的空结果话术**，不是空白页——
 * 「这是一次真实的空结果，不是页面没加载出来」。
 */
export function buildProseVM(body: string, request: string): ProseVM {
  const source = body.trim()
  const echo = echoRequest(request)
  if (!source) {
    return {
      empty: show(null, 'empty', '后端没有送可读正文'),
      heading: hide<string>('empty'),
      echo: text(echo, '没有可回声的用户原话'),
      text: '',
      chars: body.length,
    }
  }
  const lines = source.split('\n')
  const firstAt = lines.findIndex((l) => l.trim())
  const first = firstAt >= 0 ? lines[firstAt].trim() : ''
  const heading = /^#{1,3}\s+/.test(first) ? first.replace(/^#{1,3}\s+/, '') : ''
  const restText = heading ? lines.slice(firstAt + 1).join('\n').trim() : source
  return {
    empty: hide<null>('present', '有正文'),
    heading: heading ? show(heading) : hide<string>('empty', '首行不是 Markdown 标题'),
    echo: text(echo, '没有可回声的用户原话'),
    text: restText,
    chars: body.length,
  }
}
