import { rangeLabel, trendLabel, type WeekView } from '../data/firstScreen.ts'
import { Icon } from '../ui/Icon.tsx'

/**
 * 候选轮的「本周已做过」（WeekBlock 的候选支，2026-10-14 第二轮拆出）。
 *
 * 为什么拆文件：WeekBlock 本体是 147 行，已经贴着 check-layers 的 150 行规模上限
 * （它上一次也是因为这个上限从 AskRail 拆出来的）。候选轮要按设计稿把内容
 * **并排分两列**，再往本体里塞就会超限——拆的是"这一支"，首屏那一支留在
 * WeekBlock 里逐字不动。
 *
 * 与首屏那一支的差别（只重排说法与层级，**一个数都不新增**）：
 *   ① 「营养状况」与「常吃菜品」并排成左右两列——稿上就是这么排的。右栏加宽到
 *      369 之后每列约 175px，菜名放得下；这是"加宽右栏"与"内部两列"必须同时
 *      成立的原因（只做两列会把菜名折断，那正是上一版的病灶）。
 *   ② 「长期趋势」与「护栏触发」各自独占整行、做成浅底 + 左竖线的次级块，
 *      各带一句口径说明；首屏那两行仍是平铺的一行字。
 *   ③ 小节标题「本周实测 · 逐餐判定」用 stat() 的说法（全绿写「7 餐全绿」）。
 */
export function WeekBlockCand({ week }: { week: Exclude<WeekView, { isEmpty: true }> }) {
  // 逐餐判定的说法：三项全绿写「7 餐全绿」，有黄/红写「绿 6 · 黄 1」。
  // 用的就是 .fs-lights 里已经有的那三个计数，没有第四个来源。
  const stat = (l: { green: number; yellow: number; red: number }) =>
    l.red === 0 && l.yellow === 0
      ? `${l.green} 餐全绿`
      : [l.green > 0 && `绿 ${l.green}`, l.yellow > 0 && `黄 ${l.yellow}`, l.red > 0 && `红 ${l.red}`]
          .filter(Boolean)
          .join(' · ')
  // 三项都判不出来才说「长期趋势 · 数据不足」；只要有一项有结论，标题就不带这个词
  // （否则标题会对另一项撒谎）。
  const allInsuff = week.trends.length > 0 && week.trends.every((t) => trendLabel(t.state) === '数据不足')

  return (
    <section className="fs-block" data-block="week">
      <h3 className="fs-title"><Icon name="calendar" />本周已做过</h3>
      <p className="fs-weekhead">
        <b className="fs-count">{week.meals}</b>
        <span className="fs-unit">餐</span>
        {week.range && <span className="fs-range">{rangeLabel(week.range)}</span>}
      </p>

      {/* 两列：左"这一周每餐判成什么"，右"这周常做什么菜"。两者都是"数据面"，
          并排是为了让长清单不再占满整栏高度（候选页右栏比左栏高一截的根源）。 */}
      <div className="fs-cols">
        <div className="fs-col">
          <p className="fs-sub">本周实测 · 逐餐判定</p>
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
                  <span className="fs-dot" aria-hidden="true" />
                  <span className="fs-lname">{l.nutrient}</span>
                  <span className="fs-lstat">{stat(l)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="fs-col">
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
        </div>
      </div>

      {/* 趋势/护栏照实写、不编概括语（稿里"平稳，整体良好"是设计示意，真实判据是
          后端给的四态）；两句说明只解释口径、不加任何新数：趋势说明写的是后端
          为什么判不出来，护栏说明写的是它判的是"要求与禁忌有没有命中"——
          **不是**营养是否达标。 */}
      {week.trends.length > 0 && (
        <div className="fs-layer" data-layer="trend">
          <p className="fs-layer-title">长期趋势{allInsuff ? ' · 数据不足' : ''}</p>
          <p className="fs-trends">
            {week.trends.map((t) => `${t.label} ${trendLabel(t.state)}`).join('、')}
          </p>
          {allInsuff && <p className="fs-why">最近七天的记录还太少，够不上趋势判定。</p>}
        </div>
      )}
      {week.guardrail > 0 && (
        <div className="fs-layer" data-layer="guard">
          <p className="fs-layer-title">护栏触发 {week.guardrail} 次</p>
          <p className="fs-why">
            护栏判的是「要求与禁忌有没有命中」（过敏原、共同忌口这类硬约束），不是这一餐营养达不达标。
          </p>
        </div>
      )}
    </section>
  )
}
