import { rangeLabel, trendLabel, type WeekView } from '../data/firstScreen.ts'
import { Icon } from '../ui/Icon.tsx'
import { WeekBlockCand } from './WeekBlockCand.tsx'

/**
 * B3 本周（首屏四块里的强度二块 · 契约 v2.1 §1）。
 *
 * 从 AskRail.tsx 拆出（2026-09-28）：四块挤一个文件到 171 行，撞了
 * check-layers 的 150 行规模上限。拆的是**最大那一块**，其余三块留在
 * AskRail —— 分块粒度随块本身大小走，不为了凑行数把小块也拆散。
 *
 * 2026-10-14：候选轮那一支再拆一次，落到 blocks/WeekBlockCand.tsx。原因是同一道
 * 上限——本文件当时 147 行，候选轮要按设计稿把内容并排分两列，塞进来必超限。
 * **本文件从此只剩首屏那一支**，且它的 JSX 与拆分前逐字相同：首屏有 9 项冻结
 * 几何，任何重排都会当场破。候选轮的重排说明见 WeekBlockCand.tsx。
 *
 * 三条属于本块的纪律：
 *   1 `has_data=false` 走**明确空态**（内容性空，要占位说明），不是隐藏整块
 *      ——这是四块里唯一一块「空也要说话」的，因为它空的含义是「这周还没吃饭」，
 *      与「系统没这项数据」不是一回事（契约 §1 / §3）。两个分支共用这条。
 *   2 红绿灯是**实心状态点 + 中文状态字**（用户 2026-09-28 拍板改实心，
 *      取代此前"空心、禁止实心"的旧判定；lave 同日放行并给硬边界：实心
 *      **仅限状态点**，品牌 accent 仍禁填充，语义色不得扩到卡片底/按钮底/
 *      标签底）。判据仍是「绿 2 · 黄 1」那串字，颜色只作辅助（契约 §4）。
 *   3 趋势照实写「数据不足」，**不画趋势线**——后端给的全是 insufficient，
 *      画一条上升线就是替后端编结论。
 */
export function WeekBlock({ week, variant = 'ask' }: { week: WeekView; variant?: 'ask' | 'candidate' }) {
  // 空态两个分支共用：空态的 WeekView 没有 trends/dishes 字段（类型上是两个分支），
  // 在窄化之前读它就是拿一个不存在的字段。
  if (week.isEmpty) {
    return (
      <section className="fs-block" data-block="week">
        <h3 className="fs-title"><Icon name="calendar" />{variant === 'candidate' ? '本周已做过' : '本周'}</h3>
        <p className="fs-note">{week.message}</p>
      </section>
    )
  }
  // 候选轮整块交给 WeekBlockCand（同一份数据的另一种说法：候选页问的是
  // 「凭什么给你这三个」，所以要避开重复；首屏问的是"我懂你"）。
  if (variant === 'candidate') return <WeekBlockCand week={week} />

  return (
    <section className="fs-block" data-block="week">
      <h3 className="fs-title"><Icon name="calendar" />本周</h3>
      <p className="fs-weekhead">
        <b className="fs-count">{week.meals}</b>
        <span className="fs-unit">餐</span>
        {week.range && <span className="fs-range">{rangeLabel(week.range)}</span>}
      </p>

      {/* 2026-10-10 设计稿重对版：本周卡补稿的**小节层级**——「营养状况」
          「常吃菜品」两个小节标题 + 趋势/护栏行前的分隔线。此前内容是平铺的，
          与稿的"卡内分节"读感差一档；DOM 只加标题行与类名，数据与判据不动。 */}
      <p className="fs-sub">营养状况</p>
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
                {l.green > 0 && <>绿{l.green}</>}
                {l.yellow > 0 && <> · 黄{l.yellow}</>}
                {l.red > 0 && <> · 红{l.red}</>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {week.dishes.length > 0 && (
        <>
          <p className="fs-sub">常吃菜品</p>
          <ul className="fs-dishes">
            {week.dishes.map(([name, n]) => (
              <li key={name}>
                <span className="fs-dname">{name}</span>
                <b className="fs-dnum">{n}</b>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* 趋势/护栏行照实写、不编概括语（稿里"平稳，整体良好"是设计示意，
          真实判据是后端给的四态）；两行前各一条分隔线，对应稿的节间 hairline。 */}
      {week.trends.length > 0 && (
        <p className="fs-note fs-rule">
          趋势 · {week.trends.map((t) => `${t.label} ${trendLabel(t.state)}`).join('、')}
        </p>
      )}
      {week.guardrail > 0 && <p className="fs-note fs-rule">护栏触发 {week.guardrail} 次</p>}
    </section>
  )
}
