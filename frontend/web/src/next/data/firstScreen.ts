/**
 * 首屏四块（待确认候选 / 家庭档案 / 本周 / 冰箱）的数据拉取与推导。
 *
 * ══ 为什么单独一个文件 ═══════════════════════════════════════════════════
 * household.ts 只管「家庭 + 冰箱」，而首屏还要本周报告与待确认候选。
 * 两者的数据源、空值语义都不一样，塞进 household 会让那个文件变成杂物间。
 *
 * 两条纪律（与 household.ts 同源，不能丢）：
 *   1 只读，不写。任何一侧失败都退化成 null / 空数组，不抛错、不编造。
 *      **本文件全程不碰写接口**——B1 的确认/忽略已由用户拍板延后（契约 §1/§9 W1）。
 *   2 只返回值本身，标签与版式由 blocks 给。不写「照顾全家」这类替系统承诺的话。
 *
 * 空值语义按契约 §1 分三类，**不要合并成同一种「没数据」**：
 *   · candidates / members / items 为空 ⇒ 返回 null ⇒ 调用方整块不渲染
 *   · 本周 has_data=false             ⇒ 返回 isEmpty 真 ⇒ 显示明确空态（内容性空）
 *   · 接口失败或未返回                ⇒ 返回 null ⇒ 不渲染、不留白
 */

import { useEffect, useState } from 'react'
import {
  fetchPendingMemoryCandidates,
  fetchWeeklyReport,
} from '../../api/client.ts'
import { type FamilyData } from './household.ts'

export type Weekly = Awaited<ReturnType<typeof fetchWeeklyReport>>
export type PendingCandidate = Awaited<ReturnType<typeof fetchPendingMemoryCandidates>>[number]

/* ── 数据拉取 ─────────────────────────────────────────────────────────── */

/** 首屏四块中的两块（本周 + 待确认）各只拉一次。家庭与冰箱沿用 useHousehold()。 */
export function useFirstScreen(): {
  weekly: Weekly | null
  candidates: PendingCandidate[]
} {
  const [weekly, setWeekly] = useState<Weekly | null>(null)
  const [candidates, setCandidates] = useState<PendingCandidate[]>([])

  useEffect(() => {
    let alive = true
    fetchWeeklyReport()
      .then((r) => {
        if (alive) setWeekly(r)
      })
      .catch(() => {})
    fetchPendingMemoryCandidates()
      .then((list) => {
        if (alive) setCandidates(list)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  return { weekly, candidates }
}

/* ── B1 待确认候选（只读） ─────────────────────────────────────────────── */

/** 取第一条 pending。全部处理完（或失败）⇒ null ⇒ 整块不渲染。 */
export function pendingCandidate(list: PendingCandidate[]): PendingCandidate | null {
  return list.find((c) => c.status === 'pending') ?? null
}

/** 候选里的人：后端给的是 member 名字，直接显示，不替它查档案对齐措辞。 */
export function candidateView(c: PendingCandidate | null): {
  member: string
  source: string
  severity: string
} | null {
  if (!c) return null
  return {
    member: c.member,
    source: c.source_text,
    severity: c.severity,
  }
}

/* ── B2 家庭档案（结构化，给 dl 定义表格用） ──────────────────────────── */

export type FamilyMemberRow = {
  id: string
  name: string
  isActive: boolean
  /** 数字类事实（岁数 / 身高体重），mono 右对齐排。 */
  digits: string[]
  /** 条件与目标（孕妇 / 增肌），描边小标签。 */
  tags: string[]
}

/**
 * 一人一组 dt/dd：dt = 姓名 + 条件标签，dd = 数字事实。
 *
 * 与 household.ts 的 familyFacts 分工明确：那个返回**压扁成一行的字符串**，
 * 给等待页那种「两行淡字」用；这里保留结构，因为首屏要按契约 B2 排成
 * 定义表格（数字等宽、标签描边、激活成员下划线）——压扁后这三样都做不了。
 */
export function familyRows(
  family: FamilyData | null,
): { members: FamilyMemberRow[]; shared: string[] } | null {
  if (!family || family.members.length === 0) return null

  const members = family.members.map((m) => {
    const p = m.profile
    const digits: string[] = []
    if (p.basic.age) digits.push(`${p.basic.age}岁`)
    // 身高、体重**各自独立**显示。旧写法（household.ts 的 familyFacts 同款条件）
    // 要求两者都非空才写，可小美的 weight_kg 是 null —— 于是她唯一的 165cm
    // 被一起吞掉，右栏只剩「小美 · 孕妇 · 30岁」。有啥显示啥，各自成项，
    // 也自然不会出现「176cm · —」那种半截话（那才是原条件真正想防的）。
    if (p.basic.height_cm) digits.push(`${p.basic.height_cm}cm`)
    if (p.basic.weight_kg) digits.push(`${p.basic.weight_kg}kg`)
    const tags = [...p.conditions]
    if (p.goal) tags.push(p.goal)
    return {
      id: m.id,
      name: m.name,
      isActive: m.id === family.active_id,
      digits,
      tags,
    }
  })

  // 共同忌口只写一次：两个人都忌芹菜时，写两遍只是噪音。
  const shared = [...new Set(family.members.flatMap((m) => m.profile.dislikes))]
  return { members, shared }
}

/* ── B3 本周 ──────────────────────────────────────────────────────────── */

/** 一个营养素的各色计数（green/yellow/red 来自复合键拆分）。 */
export type LightCounts = Array<{ nutrient: string; green: number; yellow: number; red: number }>

export type WeekView =
  | { isEmpty: true; message: string }
  | {
      isEmpty: false
      range: [string, string] | null
      meals: number
      dishes: [string, number][]
      /**
       * 营养灯：**按营养素聚合**后的计数。
       *
       * ⚠ 后端给的 lights 不是 `Record<营养素, 次数>` —— types.ts 只写
       * `Record<string, number>`，但 2026-09-27 实测真实 key 是**复合键**
       * 「营养素:颜色」（如 `"钠:green": 2`、`"脂肪:yellow": 1`）。
       * 直接 Object.entries 出来会把 `"钠:green"` 当标签显示，既错又夹英文，
       * 所以必须按冒号拆开再聚合。类型与实际不符以**实测为准**。
       */
      lights: LightCounts
      /** 趋势：label → 后端给的字符串（实测全为 insufficient）。照实呈现，不画趋势线。 */
      trends: Array<{ label: string; state: string }>
      guardrail: number
      /**
       * 下面两个是**周报房间**（2026-09-29 户型图第 3 步）要、右栏摘要不用的：
       * 房间页有版面放完整建议与反馈计数，右栏 278px 放不下，不是漏了。
       */
      recommendations: string[]
      feedback: { count: number; tags: Record<string, number> } | null
    }

/**
 * 把 `"钠:green": 2` 这类复合键拆成营养素 + 各色次数。
 * 从**最后一个**冒号切，营养素名里万一带冒号也不会被切碎。
 */
function parseLights(raw: Record<string, number>): LightCounts {
  const map = new Map<string, { green: number; yellow: number; red: number }>()
  for (const [key, count] of Object.entries(raw)) {
    const at = key.lastIndexOf(':')
    const nutrient = at >= 0 ? key.slice(0, at) : key
    const level = at >= 0 ? key.slice(at + 1) : ''
    const cur = map.get(nutrient) ?? { green: 0, yellow: 0, red: 0 }
    if (level === 'yellow') cur.yellow += count
    else if (level === 'red') cur.red += count
    else cur.green += count
    map.set(nutrient, cur)
  }
  return [...map].map(([nutrient, c]) => ({ nutrient, ...c }))
}

export function weekView(r: Weekly | null): WeekView | null {
  if (!r) return null
  if (!r.has_data) {
    // 内容性空：本周确实没有记录，要显示明确空态，**不是**隐藏整块。
    return { isEmpty: true, message: r.message || '本周还没有用餐记录' }
  }
  return {
    isEmpty: false,
    range: r.range ?? null,
    meals: r.meals ?? 0,
    dishes: r.top_dishes ?? [],
    lights: parseLights(r.lights ?? {}),
    trends: Object.entries(r.light_trends ?? {}).map(([label, state]) => ({ label, state })),
    guardrail: r.guardrail_triggers ?? 0,
    recommendations: r.recommendations ?? [],
    feedback: r.feedback_summary ?? null,
  }
}

/**
 * 周报房间专用：只拉 weekly，带加载 / 失败态。
 *
 * 与 useFirstScreen 各拉各的——两者挂载时机不同（今晚房常驻、周报房按需），
 * 共享一份要加缓存层：多一次 GET，却换来「改一处两个房间一起变」的状态耦合，
 * 不划算。接口是只读 GET，浏览器侧成本可忽略。
 */
export function useWeeklyReport(): {
  view: WeekView | null
  loading: boolean
  error: string | null
} {
  const [view, setView] = useState<WeekView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchWeeklyReport()
      .then((r) => {
        if (!alive) return
        setView(weekView(r))
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  return { view, loading, error }
}

/** 趋势文案照实写。实测后端给的是 insufficient —— 中文化，**不补任何推断**。 */
export function trendLabel(state: string): string {
  if (state === 'insufficient') return '数据不足'
  if (state === 'sufficient') return '数据充足'
  return state
}

/** 日期区间：2026-09-20 → 09-20。只截不改语义。 */
export function rangeLabel(range: [string, string] | null): string | null {
  if (!range) return null
  const short = (s: string) => (s.length >= 10 ? s.slice(5) : s)
  return `${short(range[0])} – ${short(range[1])}`
}

/* ── B4 冰箱 ──────────────────────────────────────────────────────────── */

export function fridgeView(items: string[]): { count: number; names: string } | null {
  if (items.length === 0) return null
  return { count: items.length, names: items.join('、') }
}
