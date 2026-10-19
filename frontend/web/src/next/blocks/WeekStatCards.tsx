/**
 * C 设计稿的卡 1「本周餐次」与卡 2「营养达标情况（按餐次）」。
 *
 * 两张卡都**不做任何新算法**：
 *   · 卡 1 的「较上周」直接用后端 `prev.meals`；上周零记录（`prev.meals === 0`）
 *     写「上周无记录」，**不写 +0**——上次没记不是"没有变化"。
 *     人均是按档案里的成员数除出来的，成员数为 0 时不显示这一行（除零）。
 *   · 卡 2 的比例条**一餐一格**，格色取那一顿最差的那盏灯（有红取红、无红有黄取黄、
 *     全绿取绿）。这是**展示口径**，不是达标判据——达标仍然只看 `on_target`
 *     （它由后端 `is_meal_on_target` 判定，全后端只此一处）。
 *     之所以不按"灯"铺格：一顿饭同时有钠/糖/脂肪三盏灯，按灯铺的话 8 餐会变成
 *     24 个格子，"绿 6 黄 1"这种读法就对不上了。按餐铺，格数 = 餐数，一眼能数。
 *
 * 2026-10-19 用户判「绿 6 / 黄 13 这堆点不好看」，本卡重做为**能量条式比例条**：
 *   · 旧版问题不是配色，是**同一件事说了两遍**——19 个圆点已经等于"19 餐"，
 *     右侧又用文字写一遍"绿 6 黄 13"，还带三行图例占掉半卡宽度；圆点换行后
 *     末行残缺（11+8），比例读不出来，整卡像散落的糖豆。
 *   · 新版：一条由 19 个小段拼起来的条（段长均分、段间 2px 缝），**长度即比例**；
 *     条下是一行三档读数（色块 + 名称 + 等宽数字）。比例交给条，数量交给数字，
 *     不再有重复的文字汇总。格数与口径一字未动，改的只是呈现。
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
  /* 文案口径（2026-10-19 改）：这里的统计单位是**一道菜一条记录**，不是"一顿饭"。
   * 实测本周 168 条 ÷ 4 人 = 人均 42，而一周只有 21 顿——写"42 餐/周"会让人算不通。
   * 「↑ 126」也要改：那个箭头在常见字号下和数字 1 几乎分不开，被读成 "1126 餐"。 */
  let prevLine = '上周无记录'
  if (prev && prev.meals > 0) {
    const delta = meals - prev.meals
    if (delta === 0) prevLine = `与上周持平（${prev.meals} 道）`
    else prevLine = delta > 0 ? `比上周多 ${delta} 道` : `比上周少 ${-delta} 道`
  }
  return (
    <section className="wk-card is-count">
      <CardHead n="1" title="本周记录" />
      <p className="wk-big">
        <b>{meals}</b>
        <i>道</i>
      </p>
      <p className="wk-card-sub">{prevLine}</p>
      {perHead === null ? null : <p className="wk-card-foot">平均每人 {perHead} 道</p>}
    </section>
  )
}

export function WeekLights({ mealsDetail }: { mealsDetail: MealRow[] }) {
  const cells: Array<{ c: 'g' | 'y' | 'r'; key: string }> = []
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
    cells.push({ c, key: m.ts + m.dish })
  }
  if (cells.length === 0) return null
  const rated = cells.length
  // 读屏用整句话，视觉用条：条上的小段对读屏是噪声，所以整条给一个 aria-label。
  const summary = [
    green > 0 ? `达标 ${green} 道` : '',
    yellow > 0 ? `基本达标 ${yellow} 道` : '',
    red > 0 ? `未达标 ${red} 道` : '',
  ]
    .filter(Boolean)
    .join('、')
  return (
    <section className="wk-card is-lights">
      {/* 兜底卡（后端没给 meal_grid 时才出现）编号与 WeekMealGrid 保持一致 = 3；
          口径是「按菜品」：循环的是 mealsDetail，一条 = 一道菜。呈现沿用 2026-10-19
          用户裁决后的能量条（旧点阵的段色/段数口径不变，只换了画法）。 */}
      <CardHead n="3" title="营养达标情况" note="（按菜品）" />
      <div className="wk-meter" role="img" aria-label={`本周 ${rated} 道菜的营养灯：${summary}`}>
        {cells.map((p) => (
          <i className="wk-meter-cell" data-c={p.c} key={p.key} />
        ))}
      </div>
      <ul className="wk-meter-key">
        {green > 0 ? (
          <li data-c="g">
            <i aria-hidden="true" />
            达标
            <b>{green}</b>
          </li>
        ) : null}
        {yellow > 0 ? (
          <li data-c="y">
            <i aria-hidden="true" />
            基本达标
            <b>{yellow}</b>
          </li>
        ) : null}
        {red > 0 ? (
          <li data-c="r">
            <i aria-hidden="true" />
            未达标
            <b>{red}</b>
          </li>
        ) : null}
      </ul>
      <p className="wk-card-foot">
        本周共 {rated} 道菜有营养评估
        {unrated > 0 ? `，另有 ${unrated} 道菜没给营养灯` : ''}
      </p>
    </section>
  )
}
