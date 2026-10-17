import { Icon } from '../ui/Icon'
import { SparkLine } from '../ui/SparkLine.tsx'
import { mealLevel, mealStamp } from '../data/weekFacts.ts'
import { trendLabel } from '../data/firstScreen.ts'
import type { MemberWeekView } from '../data/firstScreen.ts'

/**
 * 成员详情的**内容**（2026-10-15）。壳在 MemberWeekDrawer。
 *
 * 五段：达标 → 趋势 → 常做的菜 → 每一顿 → 建议。顺序是按"先结论后证据"排的
 * ——用户点进来最想知道的是一句"这位这一周怎么样"，逐餐明细是支撑它的原始记录。
 *
 * 三条诚实纪律（与全仓一致，这里逐条落地）：
 *   · 后端没给分子分母时显示「待补」，**不写 0%**："还没数据"和"一餐都没达标"
 *     是两件事，写成 0% 就是替后端下了它没下的结论（NutritionProgress 的注释
 *     已经把这条写成契约）。
 *   · 趋势线走后端 light_series 真数据，null 断线**不插值**；全是 null 时退化成
 *     文字、不画假线。
 *   · 逐餐明细里"还有谁一起吃"照 `members` 如实写。**空数组时什么都不说**——
 *     不写"独自吃"，那是从"没登记"推出来的结论，而记录里并没有这件事。
 */
export function MemberWeekDetail({
  name,
  view,
}: {
  name: string
  view: Extract<MemberWeekView, { isEmpty: false }>
}) {
  return (
    <>
      <div className="wk-dr-sec">
        <p className="wk-k">达标进度</p>
        {view.progress && view.progress.numerator != null && view.progress.denominator != null ? (
          <p className="wk-dr-prog">
            <b>{view.progress.numerator}</b>
            <span> / {view.progress.denominator} 餐</span>
          </p>
        ) : (
          <p className="wk-dr-todo">待补</p>
        )}
      </div>

      {view.trends.length > 0 && (
        <div className="wk-dr-sec">
          <p className="wk-k">
            <Icon name="chart" />
            <span>趋势</span>
          </p>
          <ul className="wk-trends">
            {view.trends.map((t) => {
              const s = view.series.find((x) => x.label === t.label)
              const hasLine = !!s && s.values.some((v) => v != null)
              return (
                <li className="wk-trend" key={t.label}>
                  <span className="wk-tname">{t.label}</span>
                  <span className="wk-tstate">{trendLabel(t.state)}</span>
                  {hasLine ? (
                    <SparkLine values={s.values} state={t.state} />
                  ) : (
                    <span className="wk-tline-none" aria-hidden="true">
                      无观测
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {view.dishes.length > 0 && (
        <div className="wk-dr-sec">
          <p className="wk-k">
            <Icon name="hat" />
            <span>常做的菜</span>
          </p>
          <ul className="wk-dr-dishes">
            {view.dishes.map(([dish, n]) => (
              <li key={dish}>
                <span>{dish}</span>
                <b>{n}</b>
              </li>
            ))}
          </ul>
        </div>
      )}

      {view.mealsDetail.length > 0 && (
        <div className="wk-dr-sec">
          <p className="wk-k">
            <Icon name="clock" />
            <span>每一顿</span>
          </p>
          <ul className="wk-dr-meals">
            {view.mealsDetail.map((m) => {
              /* 把"自己"从同桌名单里减掉：后端给的是这一顿的全部用餐人，
                 而用户是在看**这个人**的详情，"我和我自己一起吃"读起来是错的。 */
              const others = m.members.filter((n) => n !== name)
              return (
                <li className="wk-dr-meal" key={m.ts}>
                  <span className="wk-meal-ts">{mealStamp(m.ts)}</span>
                  <span className="wk-dr-meal-dish">{m.dish}</span>
                  <span className="wk-meal-lights">
                    {m.lights.map((l) => {
                      const lv = mealLevel(l.level)
                      return (
                        <span className="wk-meal-light" key={l.nutrient}>
                          {l.nutrient}
                          <i className="wk-pip" data-c={lv.c} />
                          {lv.label}
                        </span>
                      )
                    })}
                  </span>
                  <span className="wk-meal-flag">{m.onTarget ? '达标' : '未达标'}</span>
                  {others.length > 0 && (
                    <span className="wk-meal-who">和 {others.join('、')} 一起</span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {view.recommendations.length > 0 && (
        <div className="wk-dr-sec">
          <p className="wk-k">
            <Icon name="pin" />
            <span>建议</span>
          </p>
          <ul className="wk-list">
            {view.recommendations.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
