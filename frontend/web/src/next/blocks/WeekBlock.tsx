import { rangeLabel, trendLabel, type WeekView } from '../data/firstScreen.ts'

/**
 * B3 本周（首屏四块里的强度二块 · 契约 v2.1 §1）。
 *
 * 从 AskRail.tsx 拆出（2026-09-28）：四块挤一个文件到 171 行，撞了
 * check-layers 的 150 行规模上限。拆的是**最大那一块**，其余三块留在
 * AskRail —— 分块粒度随块本身大小走，不为了凑行数把小块也拆散。
 *
 * 三条属于本块的纪律：
 *   1 `has_data=false` 走**明确空态**（内容性空，要占位说明），不是隐藏整块
 *      ——这是四块里唯一一块「空也要说话」的，因为它空的含义是「这周还没吃饭」，
 *      与「系统没这项数据」不是一回事（契约 §1 / §3）。
 *   2 红绿灯是**实心状态点 + 中文状态字**（用户 2026-09-28 拍板改实心，
 *      取代此前"空心、禁止实心"的旧判定；lave 同日放行并给硬边界：实心
 *      **仅限状态点**，品牌 accent 仍禁填充，语义色不得扩到卡片底/按钮底/
 *      标签底）。判据仍是「绿 2 · 黄 1」那串字，颜色只作辅助（契约 §4）。
 *   3 趋势照实写「数据不足」，**不画趋势线**——后端给的全是 insufficient，
 *      画一条上升线就是替后端编结论。
 */
export function WeekBlock({ week }: { week: WeekView }) {
  if (week.isEmpty) {
    return (
      <section className="fs-block" data-block="week">
        <h3 className="fs-title">本周</h3>
        <p className="fs-note">{week.message}</p>
      </section>
    )
  }
  return (
    <section className="fs-block" data-block="week">
      <h3 className="fs-title">本周</h3>
      <p className="fs-weekhead">
        <b className="fs-count">{week.meals}</b>
        <span className="fs-unit">餐</span>
        {week.range && <span className="fs-range">{rangeLabel(week.range)}</span>}
      </p>

      {week.lights.length > 0 && (
        <ul className="fs-lights">
          {week.lights.map((l) => (
            <li
              key={l.nutrient}
              className="fs-light"
              /* 主导状态决定圆环颜色；**它只是辅助**——真正的判据是旁边
                 「绿 2 · 黄 1」那串中文，颜色单独传不了态（契约 §4）。 */
              data-level={l.red > 0 ? 'red' : l.yellow > 0 ? 'yellow' : 'green'}
            >
              {/* 9px 实心点（用户 2026-09-28 拍板；lave 硬边界：实心仅限状态点，
                  品牌 accent 仍禁填充，语义色不得扩到卡片底/按钮底/标签底）。 */}
              <span className="fs-dot" aria-hidden="true" />
              <span className="fs-lname">{l.nutrient}</span>
              <span className="fs-lstat">
                {l.green > 0 && <>绿 {l.green}</>}
                {l.yellow > 0 && <> · 黄 {l.yellow}</>}
                {l.red > 0 && <> · 红 {l.red}</>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {week.dishes.length > 0 && (
        <ul className="fs-dishes">
          {week.dishes.map(([name, n]) => (
            <li key={name}>
              <span className="fs-dname">{name}</span>
              <b className="fs-dnum">{n}</b>
            </li>
          ))}
        </ul>
      )}

      {week.trends.length > 0 && (
        <p className="fs-note">
          趋势 · {week.trends.map((t) => `${t.label} ${trendLabel(t.state)}`).join('、')}
        </p>
      )}
      {week.guardrail > 0 && <p className="fs-note">护栏触发 {week.guardrail} 次</p>}
    </section>
  )
}
