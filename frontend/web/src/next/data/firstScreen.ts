/**
 * 首屏四块（待确认候选 / 家庭档案 / 本周 / 冰箱）的数据拉取与推导。
 *
 * ══ 为什么单独一个文件 ═══════════════════════════════════════════════════
 * household.ts 只管「家庭 + 冰箱」，而首屏还要本周报告与待确认候选。
 * 两者的数据源、空值语义都不一样，塞进 household 会让那个文件变成杂物间。
 *
 * 两条纪律（与 household.ts 同源，不能丢）：
 *   1 只读，不写。任何一侧失败都退化成 null / 空数组，不抛错、不编造，
 *      **但也不许静默**：失败原因经 readError 交出去，读取本身按阶梯退避重读（见下）。
 *      **本文件全程不碰写接口**——B1 的确认/忽略已由用户拍板延后（契约 §1/§9 W1）。
 *   2 只返回值本身，标签与版式由 blocks 给。不写「照顾全家」这类替系统承诺的话。
 *
 * 空值语义按契约 §1 分三类，**不要合并成同一种「没数据」**：
 *   · candidates / members / items 为空 ⇒ 返回 null ⇒ 调用方整块不渲染
 *   · 本周 has_data=false             ⇒ 返回 isEmpty 真 ⇒ 显示明确空态（内容性空）
 *   · 接口失败或未返回                ⇒ 返回 null ⇒ 不渲染那一块，但 readError 有话说
 *     （2026-10-12 补：原先这里与上一类在页面上长得一模一样，用户分不清"该等"
 *      还是"这周真没吃饭"——所以第三类必须交出一个可呈现的原因。）
 */

import { useEffect, useState } from 'react'
import {
  fetchMemberWeekly,
  fetchPendingMemoryCandidates,
  fetchWeeklyReport,
} from '../../api/client.ts'
import { type FamilyData } from './household.ts'
import { dislikesIntersection } from './fieldFacts.ts'
import type { MemberWeeklyReport, NutritionProgress } from '../../types.ts'
import { readErrorLine, startRetryRead } from './readLoop.ts'

export type Weekly = Awaited<ReturnType<typeof fetchWeeklyReport>>
export type PendingCandidate = Awaited<ReturnType<typeof fetchPendingMemoryCandidates>>[number]

/* ── 数据拉取 ─────────────────────────────────────────────────────────── */

/** 首屏四块中的两块（本周 + 待确认）各只拉一次。家庭与冰箱沿用 useHousehold()。
 *
 * 读取必须会自愈（2026-10-12 修，与 household.ts 同一套口径、同一份阶梯）：
 *   · 这一读同样发生在**页面加载那一刻**，而那一刻未必是后端可用的一刻
 *     ——实测后端重启窗口里有 22 个 /api/* 同时 502；
 *   · 原先两处都是 `.catch(() => {})`：失败被吞成"这块没有数据"，
 *     而右栏对 null 的处置是**整块不渲染**，于是后端恢复后「本周」「待确认」
 *     会一直空着，直到用户手动刷新（真实事故，不是预防性重构）；
 *   · 现在：失败按 retry.ts 的阶梯退避重读（1s→…→30s 封顶）直到读到；
 *     失败只记 readError，**绝不清空已经渲染出来的数据**；
 *     读到即清掉原因。循环本体在 data/readLoop.ts（纯函数，node --test 钉着）。
 *
 * 为什么这块必须与「本周 has_data=false」分开：那块是**内容性空**（这周真没吃饭），
 * 要显示明确空态；读失败则要给得出原因——两者在页面上长得一样的话，
 * 用户分不清是该等还是该去记录，排查者也看不到 HTTP 状态。 */
export function useFirstScreen(): {
  weekly: Weekly | null
  candidates: PendingCandidate[]
  /** 读失败的原因（本周 / 待确认 各一路，按实际失败的那些拼）；读到成功即清。 */
  readError: string | null
} {
  const [weekly, setWeekly] = useState<Weekly | null>(null)
  const [candidates, setCandidates] = useState<PendingCandidate[]>([])
  const [weeklyError, setWeeklyError] = useState<string | null>(null)
  const [candidatesError, setCandidatesError] = useState<string | null>(null)

  useEffect(() => {
    const stopWeekly = startRetryRead<Weekly>({
      read: fetchWeeklyReport,
      onData: (r) => {
        setWeekly(r)
        setWeeklyError(null)
      },
      onError: setWeeklyError,
    })
    const stopCandidates = startRetryRead<PendingCandidate[]>({
      read: () => fetchPendingMemoryCandidates(),
      onData: (list) => {
        setCandidates(list)
        setCandidatesError(null)
      },
      onError: setCandidatesError,
    })
    // 卸载即取消：两个循环各自清掉待发的 timer，迟到结果不再写状态。
    return () => {
      stopWeekly()
      stopCandidates()
    }
  }, [])

  return {
    weekly,
    candidates,
    readError: readErrorLine([
      ['本周', weeklyError],
      ['待确认', candidatesError],
    ]),
  }
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
  /** 后端 `profile.nutrition_progress` 的**原样**（2026-10-14 补）。
   *
   *  此前没带出来，于是收藏页成员块只能写「进度待补 · 后端没有这一项」——
   *  那是错的：字段一直有（`reports_route.py` `member_nutrition_progress`），
   *  只是这一屏没接。现在原样透传（不做换算，缺值就是 null），
   *  由 `progressShort` / `progressLine` 决定怎么显示。 */
  progress: NutritionProgress | null
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
      // 原样透传，不做换算：缺值就是 null（`memberBodyExtras` 同一条纪律）。
      progress: p.nutrition_progress ?? null,
    }
  })

  // 共同忌口只写一次：两个人都忌芹菜时，写两遍只是噪音。
  //
  // ⚠ 2026-10-14 修：这一行原先是
  //     `[...new Set(family.members.flatMap((m) => m.profile.dislikes))]`
  //   ——那是**并集**：把"任意一个人忌的"都算成全家的共同忌口，与上面这句注释
  //   写的"两个人都忌"（交集）正好相反。同一个缺陷 2026-10-05 只在 household.ts
  //   修过一次，首屏这一份漏了；真实档案两人恰好都忌芹菜，两种口径结果相同，
  //   所以它一直没露出来——只要有一天只有一个人忌香菜，首屏就会把香菜写成
  //   全家的共同忌口，把个人忌口安到全家人头上。
  //
  //   现在优先读后端算好的 `shared_dislikes`（判定已收归
  //   api/routes/preferences_route.py 的 `_shared_dislikes`），兜底也走交集。
  const shared = family.shared_dislikes ?? dislikesIntersection(family.members)
  return { members, shared }
}

/* ── B3 本周 ──────────────────────────────────────────────────────────── */

/** 一个营养素的各色计数（green/yellow/red 来自复合键拆分）。 */
export type LightCounts = Array<{ nutrient: string; green: number; yellow: number; red: number }>

/** 一格 = 一顿。`level: null` 且 records === 0 才是"没记录"，records > 0 是"记了没评估"。 */
export type MealCell = {
  level: 'green' | 'yellow' | 'red' | null
  records: number
  dishes: string[]
  bad: Array<{ dish: string; nutrient: string }>
}

/** 7 天 × 早/午/晚 的逐顿矩阵（后端 `settle_meals`）。 */
export type MealGrid = {
  range: [string, string]
  slots: string[]
  days: Array<{ date: string; weekday: string; isToday: boolean }>
  cells: MealCell[][]
  legend: { green: number; yellow: number; red: number; none: number }
  total: number
  /** 被裁掉的"今天"日期（当天完全无记录时才裁）；null = 没裁。 */
  droppedToday: string | null
}

/** 「本周 N 处超标」的一条：一顿 + 该顿里真的判红的菜与营养素。 */
export type OverItem = {
  date: string
  weekday: string
  slot: string
  records: number
  dishes: string[]
  bad: Array<{ dish: string; nutrient: string }>
  nutrients: string[]
  advice: string
}

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
      /**
       * 趋势图的逐日真数据（2026-10-10 · 周报方向稿第 3 条「要数据真实」）：
       * 营养素 → 含今天最近 7 天的绿灯占比，null = 当天无观测（画断点不插值）。
       * 与 trends（状态字符串）**并存**：trends 决定箭头文案，series 决定线的形状；
       * series 缺席或全 null 时前端退化为纯文字，绝不画假线。
       */
      series: Array<{ label: string; values: (number | null)[] }>
      /** top 菜品的成品图（后端 dish_assets 命中才有；缺席的菜走占位块）。 */
      dishImages: Record<string, string>
      guardrail: number
      /**
       * 下面两个是**周报房间**（2026-09-29 户型图第 3 步）要、右栏摘要不用的：
       * 房间页有版面放完整建议与反馈计数，右栏 278px 放不下，不是漏了。
       */
      recommendations: string[]
      feedback: { count: number; tags: Record<string, number> } | null
      /**
       * 逐餐明细（2026-10-14）：把 top_dishes 的聚合次数展开成「哪几顿」。
       * 只给**周报房间**用——右栏摘要（278px）放不下也不需要，与上面两条同理。
       * member 为 null = 那一顿没记到人，**不许**显示成「全家」。
       */
      mealsDetail: Array<{
        ts: string
        dish: string
        member: string | null
        lights: Array<{ nutrient: string; level: string }>
        onTarget: boolean
      }>
      /**
       * 成员卡片（2026-10-15 后端新增 `_member_week_rows`）。
       *
       * **家庭与每位成员是同级对象**，所以这一段与上面那些家庭字段并列，不是它们的
       * 下钻。名单来自后端档案，所以 `meals: 0` 的成员**也在列表里**——如实显示
       * 「这一周还没记到」，不因为数字是 0 就不画这张卡（不画等于说这人不在这个家）。
       *
       * lights 走同一个 parseLights 拆解（后端给的是同一套复合键），
       * 不给它第二个实现。
       */
      members: Array<{
        name: string
        meals: number
        onTarget: number
        lights: LightCounts
        shared: number
        /**
         * 上周同期这位成员自己的餐数与达标数。两个都是 0 ⇒ 上周没有他的记录，
         * 界面要写「上周无记录」——**不许**读成"上周一餐都没达标"。
         */
        prevMeals: number
        prevOnTarget: number
        /** 后端给的一句话（「需要注意」）。空串表示后端没话说，那一段就不渲染。 */
        hint: string
      }>
      /**
       * 本周达标率（0..1）。`null` = 本周零记录（空态走 isEmpty 那条路，正常到不了这里）。
       */
      rate: number | null
      /**
       * 逐周达标率，最旧在前、本周在后（C 设计稿「趋势」折线）。
       * 某周 `rate: null` ⇒ 零记录，折线在那里**断开**，不连到 0%。
       */
      rateSeries: Array<{
        label: string
        rate: number | null
        meals: number
        onTarget: number
        isCurrent: boolean
      }>
      /**
       * 上一周同期整户。`null` 表示后端没给这个键（空周）。`rate: null` = 上周零记录。
       */
      prev: { meals: number; onTarget: number; rate: number | null } | null
      /**
       * 逐顿矩阵（2026-10-19）：7 天 × 早/午/晚，一格一顿。
       *
       * 它替代了原来的 169 点阵卡：点阵是"一条记录一个点"，而记录是一道菜一笔，
       * 10-13 那 48 个点里 18 个是同一道宫保鸡丁。矩阵先把菜归并成顿。
       * `null` = 后端没给（旧后端），前端退回不渲染这张卡。
       */
      mealGrid: MealGrid | null
      /** 「本周 N 处超标」清单。空数组 = 这周没有红灯，那张卡不渲染。 */
      overList: OverItem[]
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
    series: Object.entries(r.light_series ?? {}).map(([label, values]) => ({ label, values })),
    dishImages: r.dish_images ?? {},
    guardrail: r.guardrail_triggers ?? 0,
    recommendations: r.recommendations ?? [],
    feedback: r.feedback_summary ?? null,
    mealsDetail: (r.meals_detail ?? []).map((m) => ({
      ts: m.ts,
      dish: m.dish,
      member: m.member ?? null,
      lights: m.lights ?? [],
      onTarget: m.on_target ?? false,
    })),
    members: (r.members ?? []).map((m) => ({
      name: m.name,
      meals: m.meals ?? 0,
      onTarget: m.on_target ?? 0,
      lights: parseLights(m.lights ?? {}),
      shared: m.shared ?? 0,
      prevMeals: m.prev_meals ?? 0,
      prevOnTarget: m.prev_on_target ?? 0,
      hint: m.hint ?? '',
    })),
    rate: r.rate ?? null,
    rateSeries: (r.rate_series ?? []).map((w) => ({
      label: w.label,
      rate: w.rate ?? null,
      meals: w.meals ?? 0,
      onTarget: w.on_target ?? 0,
      isCurrent: w.is_current ?? false,
    })),
    // 后端空周直接不返回这个键，所以这里要区分"没有这个键"（null）与"值是 0"。
    prev: r.prev
      ? {
          meals: r.prev.meals ?? 0,
          onTarget: r.prev.on_target ?? 0,
          rate: r.prev.rate ?? null,
        }
      : null,
    // 逐顿矩阵（2026-10-19）。缺字段的老后端 ⇒ mealGrid 为 null，卡片直接不渲染，
    // 不拿 mealsDetail 在前端拼一个替代品：那会是第二套"怎么算一顿"的实现。
    mealGrid: r.meal_grid
      ? {
          range: r.meal_grid.range,
          slots: r.meal_grid.slots ?? [],
          days: (r.meal_grid.days ?? []).map((d) => ({
            date: d.date,
            weekday: d.weekday,
            isToday: d.is_today ?? false,
          })),
          cells: r.meal_grid.cells ?? [],
          legend: r.meal_grid.legend ?? { green: 0, yellow: 0, red: 0, none: 0 },
          total: r.meal_grid.total ?? 0,
          droppedToday: r.meal_grid.dropped_today ?? null,
        }
      : null,
    overList: (r.over_list ?? []).map((o) => ({
      date: o.date,
      weekday: o.weekday,
      slot: o.slot,
      records: o.records ?? 0,
      dishes: o.dishes ?? [],
      bad: o.bad ?? [],
      nutrients: o.nutrients ?? [],
      advice: o.advice ?? '',
    })),
  }
}

/* ── B3b 成员周报详情（2026-10-15，周报页成员卡片点开）────────────────── */

/**
 * 一位成员的 7 天视图。与 `WeekView` 是**两个视角**而不是同一份的两个版本：
 * 这里只装"这位成员"的餐，分母只算他参与的顿数。
 *
 * ⚠ 刻意**没有** `lights` 这个字段：后端的成员版不返回顶层灯计数（家庭版才有），
 * 而前端要的"这一周的灯分布"能从 `mealsDetail` 里逐餐数出来。宁可让渲染层从明细
 * 现数，也不在这里凭 series 反推一个假的分布。
 */
export type MemberWeekView =
  | { isEmpty: true; name: string; message: string }
  | {
      isEmpty: false
      name: string
      meals: number
      /** 后端还不知道分子分母时 value 为 null ⇒ 页面显示「待补」，不许写成 0%。 */
      progress: NutritionProgress | null
      trends: Array<{ label: string; state: string }>
      series: Array<{ label: string; values: (number | null)[] }>
      dishes: [string, number][]
      dishImages: Record<string, string>
      recommendations: string[]
      mealsDetail: Array<{
        ts: string
        dish: string
        members: string[]
        lights: Array<{ nutrient: string; level: string }>
        onTarget: boolean
      }>
    }

export function memberWeekView(r: MemberWeeklyReport | null): MemberWeekView | null {
  if (!r) return null
  if (!r.has_data) {
    // 内容性空：后端给了原因就用它（查无此人也走这条，它是"没记到"不是异常）。
    return {
      isEmpty: true,
      name: r.name ?? '',
      message: r.message || '近 7 天还没有记到这位成员的餐次。',
    }
  }
  return {
    isEmpty: false,
    name: r.name,
    meals: r.meals ?? 0,
    progress: r.progress ?? null,
    trends: Object.entries(r.light_trends ?? {}).map(([label, state]) => ({ label, state })),
    series: Object.entries(r.light_series ?? {}).map(([label, values]) => ({ label, values })),
    dishes: r.dishes ?? [],
    dishImages: r.dish_images ?? {},
    recommendations: r.recommendations ?? [],
    mealsDetail: (r.meals_detail ?? []).map((m) => ({
      ts: m.ts,
      dish: m.dish,
      members: m.members ?? [],
      lights: m.lights ?? [],
      onTarget: m.on_target ?? false,
    })),
  }
}

/**
 * 按需拉一位成员的周报。`name` 为 null（抽屉没开）时**不发请求**，
 * 所以周报页平时只有一次 `/reports/weekly`，点开某人才多一次。
 *
 * ⚠ 换人时必须先把上一位的 view 清掉：不清的话点「小美」再点「我」，抽屉会先
 * 拿小美的数据渲染一帧（她的名字配我的数字），等请求回来才纠正。
 * 那种一闪而过的错配正好出现在用户盯着看的时候。
 */
export function useMemberWeekly(name: string | null): {
  view: MemberWeekView | null
  loading: boolean
  error: string | null
} {
  const [view, setView] = useState<MemberWeekView | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!name) {
      setView(null)
      setLoading(false)
      setError(null)
      return
    }
    let alive = true
    setView(null)
    setLoading(true)
    setError(null)
    fetchMemberWeekly(name)
      .then((r) => {
        if (!alive) return
        setView(memberWeekView(r))
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
  }, [name])

  return { view, loading, error }
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

/**
 * 趋势文案照实写（2026-10-10 补全值域并修英文泄漏）。
 *
 * 后端 `_light_trends` 的实测值域是四个：insufficient / improving / worsening /
 * stable（reports_route.py L150-159）。原实现只认前两个英文词之外的 insufficient
 * 与 sufficient，于是真实的 improving / stable **原样落到页面上**——首屏右栏
 * 「趋势 · 钠 improving、脂肪 improving」就是这么来的（英文 UI 泄漏）。
 * 箭头按设计稿周报样式（↑ 改善中 / — 稳定 / ↓ 需留意）；未知值仍原样返回，
 * 不替后端编词。
 */
export function trendLabel(state: string): string {
  if (state === 'insufficient') return '数据不足'
  if (state === 'sufficient') return '数据充足'
  if (state === 'improving') return '↑ 改善中'
  if (state === 'stable') return '— 稳定'
  if (state === 'worsening') return '↓ 需留意'
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





