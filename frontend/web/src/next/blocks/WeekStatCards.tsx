/**
 * C 设计稿的卡 1「本周餐次」与卡 2「营养达标情况（按餐次）」。
 *
 * 两张卡都**不做任何新算法**：
 *   · 卡 1 的「较上周」直接用后端 `prev.meals`；上周零记录（`prev.meals === 0`）
 *     写「上周无记录」，**不写 +0**——上次没记不是"没有变化"。
 *     人均是按档案里的成员数除出来的，成员数为 0 时不显示这一行（除零）。
 *   · 卡 2 的点阵**一餐一个点**，取那一顿最差的那盏灯（有红取红、无红有黄取黄、
 *     全绿取绿）。这是**展示口径**，不是达标判据——达标仍然只看 `on_target`
 *     （它由后端 `is_meal_on_target` 判定，全后端只此一处）。
 *     之所以不按"灯"铺点：一顿饭同时有钠/糖/脂肪三盏灯，按灯铺的话 8 餐会变成
 *     24 个点，"绿 6 黄 1"这种读法就对不上了。按餐铺，点数 = 餐数，一眼能数。
 */
import { CardHead } from './WeekCardShell'

type MealRow = {
  ts: string
  dish: string
  onTarget: boolean
  lights: Array<{ nutrient: string; level: string }>
}

/** 这一顿"最差"的那盏灯：有红取红 > 有黄取黄 > 否则绿。没有灯 → null。 */
function worstLevel(levels: string[]): 'g' | 'y' | 'r' | null {
  if (levels.length === 0) return null
  if (levels.includes('red')) return 'r'
  if (levels.includes('yellow')) return 'y'
  return 'g'
}

export function WeekMealCount({
  meals,
  memberCount,
  prev,
}: {
  meals: number
  memberCount: number
  prev: { meals: number; onTarget: number; rate: number | null } | null
}) {
  const perHead = memberCount > 0 ? Math.round((meals / memberCount) * 10) / 10 : null
  let prevLine = '上周无记录'
  if (prev && prev.meals > 0) {
    const delta = meals - prev.meals
    if (delta === 0) prevLine = `较上周 持平（${prev.meals} 餐）`
    else prevLine = delta > 0 ? `较上周 ↑ ${delta} 餐` : `较上周 ↓ ${-delta} 餐`
  }
  return (
    <section className="wk-card is-count">
      <CardHead n="1" title="本周餐次" />
      <p className="wk-big">
        <b>{meals}</b>
        <i>餐</i>
      </p>
      <p className="wk-card-sub">{prevLine}</p>
      {perHead === null ? null : <p className="wk-card-foot">人均 {perHead} 餐/周</p>}
    </section>
  )
}

export function WeekLights({ mealsDetail }: { mealsDetail: MealRow[] }) {
  const pips: Array<{ c: 'g' | 'y' | 'r'; key: string }> = []
  let green = 0
  let yellow = 0
  let red = 0
  let unrated = 0
  for (const m of mealsDetail) {
    const c = worstLevel(m.lights.map((l) => l.level))
    if (c === null) {
      unrated += 1
      continue
    }
    if (c === 'g') green += 1
    else if (c === 'y') yellow += 1
    else red += 1
    pips.push({ c, key: m.ts + m.dish })
  }
  if (pips.length === 0) return null
  const rated = pips.length
  return (
    <section className="wk-card is-lights">
      <CardHead n="2" title="营养达标情况" note="（按餐次）" />
      <div className="wk-card-lights">
        <span className="wk-pips" aria-hidden="true">
          {pips.map((p) => (
            <i className="wk-pip" data-c={p.c} key={p.key} />
          ))}
        </span>
        <div className="wk-card-legend">
          <p className="wk-card-sum">
            {green > 0 ? (
              <span className="wk-sum is-g">绿 {green}</span>
            ) : null}
            {yellow > 0 ? (
              <span className="wk-sum is-y">黄 {yellow}</span>
            ) : null}
            {red > 0 ? (
              <span className="wk-sum is-r">红 {red}</span>
            ) : null}
          </p>
          <ul className="wk-legend">
            <li>
              <i className="wk-pip" data-c="g" aria-hidden="true" />
              绿（达标）
            </li>
            <li>
              <i className="wk-pip" data-c="y" aria-hidden="true" />
              黄（基本达标）
            </li>
            <li>
              <i className="wk-pip" data-c="r" aria-hidden="true" />
              红（未达标）
            </li>
          </ul>
        </div>
      </div>
      <p className="wk-card-foot">
        本周共 {rated} 餐有营养评估
        {unrated > 0 ? `，另有 ${unrated} 餐没给营养灯` : ''}
      </p>
    </section>
  )
}
