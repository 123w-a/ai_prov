/**
 * 2026-10-11 后端新增字段的**纯推导与文案**：孕周 / 体脂率 / 成员营养进度 / 收藏时间。
 *
 * 为什么单独一个模块：这些判定的要点是"没有真值就落 null，绝不落成 0 或估算值"，
 * 而它们原本的宿主（household.ts / favorites.ts / FavView.tsx）头部 import 了 react
 * 与 api/client.ts（后者读 `import.meta.env`），在 node 里根本加载不起来——也就是
 * 最该被钉住的那部分恰好测不到。所以推导搬到这里：只依赖 type import（编译后消失），
 * 可以用仓库既有的 `node --test` 直接跑（见 fieldFacts.test.ts）。
 *
 * 数据来源与语义：
 *   · 孕周 / 体脂率 → 后端成员档案 `profile.basic.gestational_weeks` / `body_fat_rate`
 *     （api/routes/preferences_route.py）。用户录入，后端不推算；旧档案缺键 = null。
 *   · 营养进度 → 后端 `profile.nutrition_progress`
 *     （api/routes/reports_route.py member_nutrition_progress）。判据未定期间恒 null。
 *   · 收藏时间 → 后端 `favorited_at`（storage/sessions.py list_starred）。旧收藏为 null。
 */

import type { FamilyMember, NutritionProgress } from '../../types.ts'

/** 数值位的中性占位。与 ProfileView 的 TODO 同字面量：0 会被读成"真的是零"，
 *  一个像样的数字会被读成"真的是这个值"——在健康信息上两者都是撒谎。 */
export const FIELD_TODO = '待补'

/** 只认有限的 number：undefined / null / 字符串 / NaN / Infinity 一律落 null。
 *  （后端把缺字段序列化成 null，但"字段整个不在"在 JS 里是 undefined，两者都得接住。） */
const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

export interface BodyExtras {
  gestationalWeeks: number | null
  bodyFatRate: number | null
  nutrition: NutritionProgress | null
}

/** 家户级共同忌口 = **交集**（每个人都忌的那几样），不是并集。

 *  为什么放进这个纯模块：`household.ts`（等待页/档案页）与 `firstScreen.ts`（首屏）
 *  都要用它，而两个宿主都 import 了 react 与 api/client.ts，在 node 里加载不起来；
 *  这个判定恰恰是最该被钉住的那类——本仓库已经因为它错过一次：2026-10-05 只修了
 *  household 那一处，首屏那份并集实现一直留到 2026-10-14 才被发现。

 *  单人家庭返回 []：一个人的忌口是他自己的，不是全家的。
 *  任一成员没填忌口 → 空（他与任何人都没有"共同"忌口）。
 *  顺序跟随第一位成员，不排序——忌口顺序是用户自己填的，排序会让同一个值在
 *  不同次渲染里换位置。

 *  这是**兜底**：正常情况下读后端 `family.shared_dislikes`，这里只在后端字段
 *  缺席（旧响应 / 接口异常）时兜住，且绝不退回并集——宁可少写一条，
 *  不能把某个人的忌口安到全家人头上。
 */
export function dislikesIntersection(members: FamilyMember[]): string[] {
  if (members.length < 2) return []
  const lists = members.map((m) => (m.profile?.dislikes ?? []).filter(Boolean))
  if (lists.some((l) => l.length === 0)) return []
  const first = [...new Set(lists[0])]
  return first.filter((d) => lists.every((l) => l.includes(d)))
}

/** 成员身上三个新字段的**原样读取**，不做任何换算。
 *
 *  · 孕周不反推预产期（反推要在末次月经/预产期之间编一个前提）；
 *  · 体脂率不由 BMI 估（估算值看着像真值，最容易被当成体脂秤读数）；
 *  · 营养进度不由"绿灯占比"顶（那是另一个口径，后端没认可）。
 *
 *  体脂率的 0 单独判掉：后端校验是 ge=1，能出现 0 只可能是坏数据，而 `0 !== null`
 *  会让页面堂而皇之画一个「体脂率 0%」。0 落 null → 页面写「待补」。
 *  孕周的 0 是合法的（刚怀上，后端 ge=0），不跟着一起判掉。 */
export function memberBodyExtras(member: FamilyMember): BodyExtras {
  const basic = member.profile?.basic
  const fat = num(basic?.body_fat_rate)
  return {
    gestationalWeeks: num(basic?.gestational_weeks),
    bodyFatRate: fat !== null && fat > 0 ? fat : null,
    nutrition: member.profile?.nutrition_progress ?? null,
  }
}

/** 成员营养进度的一句话（档案页脚注用）。
 *
 *  有真值 → **可核对的表达**：「本周 3/5 餐达标 · 60%」——分子分母原样给出来，
 *  拿手一除就知道百分比是怎么来的（四舍五入过的 60% 不能自证）。
 *  并且**如实标注口径出处**：这数是后端按"该餐营养灯全绿"算的
 *  （api/routes/reports_route.py `is_meal_on_target`），不是"绿灯占比"、不是估算。
 *  口径改了这句话必须跟着改，否则页面就在替后端编规则。
 *
 *  value 为 null → 写「待补」，**不写 0%**：0% 读起来是"一餐都没达标"，
 *  而事实是"本周还没记到 TA 的餐次"（未归属 / 该成员这周没记过）。
 *  文案里必须把"为什么没有"说清，不许只留一个占位符——占位符会被当成待补的
 *  真数字，写清原因才是诚实的空值。
 */
export function progressLine(nutrition: NutritionProgress | null | undefined): string {
  const CRITERION = '口径：该餐营养灯全绿'
  const value = num(nutrition?.value)
  if (value === null) {
    return `本周还没有记录到该成员的餐次（未归属或本周未记录），进度待补（${CRITERION}）。`
  }
  const percent = `${Math.round(value * 100)}%`
  const n = num(nutrition?.numerator)
  const d = num(nutrition?.denominator)
  if (n === null || d === null) return `本周营养达标 ${percent}（${CRITERION}）。`
  return `本周 ${n}/${d} 餐达标 · ${percent}（${CRITERION}）。`
}

/** 成员营养进度的**短版**（收藏页成员块那种窄槽用，2026-10-14 新增）。
 *
 *  与 `progressLine` 同源、同口径（后端 `is_meal_on_target`：该餐营养灯全绿），
 *  只去掉"口径：…"那段说明——窄槽里塞不下，完整句通过 title 挂在同一元素上。
 *
 *  为什么必须有这一版：收藏页成员块此前写的是「进度待补 · 后端没有这一项」，
 *  而 `nutrition_progress` 后端一直有（reports_route.py `member_nutrition_progress`），
 *  只是 `familyRows` 没带出来。「这一屏还没接」被写成了「后端没有」——前者是没做完，
 *  后者是编造，两件事。
 *
 *  无真值时**不写 0%**，也不只留一个占位符：说清"为什么没有"，
 *  否则占位符会被当成一个待补的真数字（与 `progressLine` 同一条纪律）。
 */
export function progressShort(nutrition: NutritionProgress | null | undefined): string {
  const value = num(nutrition?.value)
  if (value === null) return '本周还没有 TA 的餐次'
  const percent = `${Math.round(value * 100)}%`
  const n = num(nutrition?.numerator)
  const d = num(nutrition?.denominator)
  return n === null || d === null ? `本周达标 ${percent}` : `本周 ${n}/${d} 餐达标 · ${percent}`
}

/** `weightLine` 只需要的字段。刻意不写在这里 import 结构：
 *  `api/client.ts` 的 `WeightSummary` 天然满足它，两边各自演进时不会互相拖住。 */
export interface WeightFactLike {
  count?: number | null
  change_kg?: number | null
  earliest?: { date?: string | null } | null
  latest?: { date?: string | null } | null
}

/** 体重记录那一行的说明（2026-10-18 新增，配身体档案页的「记录体重」）。
 *
 *  三种状态各有各的话，**任何一种都不写 `0.0kg`**：
 *  - 一条都没记 → 说清"记一次就能开始看变化"。用户在这个页面看到体重，
 *                 第一反应是"为什么是空的"，得直接告诉他怎么让它有值
 *                 （这与「目标与进度」那类无采集入口的空块刚好相反——那个该删，
 *                 这个有入口，所以留着且说清）。
 *  - 只有一条   → 说清"再记一次才能算变化"。一条记录算不出变化，写 `+0.0kg`
 *                 会被读成"体重没变"，而事实是"还比不了"（后端此时给的是 null，
 *                 不是 0 —— 与 `bmiFact` 的「空值不许变成 0」同一条纪律）。
 *  - 两条以上   → 给**可核对的表达**：起止日期 + 差值，正号显式写出来。
 *                 只报差值不给日期的话，用户没法判断这个变化是几天内发生的。
 *
 *  本函数**只陈述变化量，不判断是否正常**：孕期增重有医学推荐区间，但那是产检的
 *  事。一个膳食规划助手说"这个增速偏快"，就是越界替医生下结论。
 */
export function weightLine(summary: WeightFactLike | null | undefined): string {
  const n = num(summary?.count) ?? 0
  if (n <= 0) return '还没有体重记录，记一次就能开始看变化。'
  const change = num(summary?.change_kg)
  const earliest = summary?.earliest?.date
  const latest = summary?.latest?.date
  if (change === null || !earliest || !latest) {
    return '体重只有一条记录，再记一次才能算出变化。'
  }
  const sign = change > 0 ? '+' : ''
  return `体重较 ${earliest} ${sign}${change}kg（到 ${latest}）。`
}

/** 「收藏于」那一格的取值（收藏页用）：后端给了收藏动作时间就显示 MM-DD
 *  （与稿的粒度一致）；null / 空 / 畸形串一律给「待补」。
 *  **绝不**拿 turnedAt（那一轮提问的时间）顶：那会把"什么时候收的"说成假的。 */
export function favoritedAtLabel(favoritedAt: string | null | undefined): string {
  const s = (favoritedAt ?? '').trim()
  return s.length >= 10 ? s.slice(5, 10) : FIELD_TODO
}

/** BMI 槽的取值：算得出给值，算不出给 null 并把**缺的是哪一项**交出来。
 *  形状与 memberBodyExtras 同一条纪律：不编、不估、不拿别的字段顶。 */
export interface BmiFact {
  /** 身高体重都在才有值（一位小数）。null = 算不出。 */
  value: string | null
  /** 算不出时缺的那一项或两项（"身高" / "体重" / "身高与体重"）；算得出时 null。 */
  missing: string | null
}

/**
 * BMI = 体重 ÷ 身高²（沿用 ProfileView 原来的口径：一位小数，再多的位数是假精度）。
 *
 * ⚠ 2026-10-12 的真实缺陷（不是防御性重构）：页面原来给 BMI 槽**无条件**挂了一枚
 * 「待补」小签，于是「我」的槽渲染成 `BMI 20.3 待补` —— 20.3 是由 63kg/176cm
 * 真算出来的值，却被自己的签说成占位。这是本页最不该犯的错（把真数说成假的）。
 * 现在的口径：
 *   · 算得出 ⇒ 只给值，**不挂任何签**；
 *   · 算不出 ⇒ value = null（调用方画「待补」），同时 missing 说明缺身高还是缺体重
 *     ——「待补」两个字本身不构成解释，缺哪一项才是能让人去补的那条信息。
 *
 * 0 / 负数 / 字符串一律按"没有这一项"处理（后端身高体重是正数，0 只可能是坏数据；
 * 拿 0 落进分母还会算出 Infinity）。
 */
export function bmiFact(
  basic: { height_cm?: number | null; weight_kg?: number | null } | null | undefined,
): BmiFact {
  const h = num(basic?.height_cm)
  const w = num(basic?.weight_kg)
  const hasHeight = h !== null && h > 0
  const hasWeight = w !== null && w > 0
  if (hasHeight && hasWeight) {
    const m = h / 100
    return { value: (w / (m * m)).toFixed(1), missing: null }
  }
  const missing: string[] = []
  if (!hasHeight) missing.push('身高')
  if (!hasWeight) missing.push('体重')
  return { value: null, missing: missing.join('与') }
}
