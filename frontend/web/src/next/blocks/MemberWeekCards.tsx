/**
 * 周报页的「家庭成员（本周）」卡片区（C 设计稿那一排卡）。
 *
 * 一张卡自上而下是：姓名 → 「本周达标进度」→ 大数字 `3/5 · 60%` + 圆环 →
 * 「较上周」→ 「需要注意」。整张卡是一个 button，点进详情抽屉。
 *
 * 三条纪律：
 *
 *  1. **`meals === 0` 的成员画「待补」+ 虚线空圆，不画 0%**。这与后端
 *     「分母为 0 返回 null」是同一条纪律的两半：0% 读起来是"一餐都没达标"，
 *     而真相是"这周还没记到他"。这张卡也**不许**因为数字是 0 就消失——名单取自
 *     档案，少一张卡会被读成"这个人不在这个家里"。
 *  2. **「较上周」在 `prevMeals === 0` 时写「较上周 —」**，不写"持平"、更不写
 *     "0 餐"。上周没记录不是"没有变化"，说成持平就是把"不知道"说成"确定"。
 *  3. **`hint` 直接显示后端给的那句话**，前端不加工、不按灯自己编——那句话的
 *     措辞与判据都住在后端一处，前端再拼一套迟早与它对不上。
 *
 * 按钮里只用 span/b/i/svg（不用 div）：button 的内容模型只允许短语级元素，
 * 塞 div 进去浏览器会重排父级，交互区跟着变形。
 *
 * 这一区**不带自己的标题**（2026-10-15 修）：标题由 WeeklyView 的
 * `.wk-zone-t`「家庭成员（本周）」统一给，与「家庭整体（本周）」同款。
 * 各写一份的话两区标题会长得不一样，读者会以为成员区是家庭区的附属。
 */
import { RingProgress } from '../ui/RingProgress'
import type { LightCounts } from '../data/firstScreen.ts'

type MemberRow = {
  name: string
  meals: number
  onTarget: number
  lights: LightCounts
  shared: number
  prevMeals: number
  prevOnTarget: number
  hint: string
}

/** 「较上周」那一行的文案。上周零记录时给「—」，不给"持平"。 */
function prevText(m: MemberRow): string {
  if (m.prevMeals === 0) return '较上周 —'
  const delta = m.meals - m.prevMeals
  if (delta === 0) return `较上周 持平（${m.prevMeals} 餐）`
  return delta > 0 ? `较上周 ↑ ${delta} 餐` : `较上周 ↓ ${-delta} 餐`
}

export function MemberWeekCards({
  members,
  onOpen,
}: {
  members: MemberRow[]
  onOpen: (name: string) => void
}) {
  if (members.length === 0) return null
  return (
    <div className="wk-sec is-crew" data-block="crew">
      <ul className="wk-mems">
        {members.map((m) => {
          const blank = m.meals === 0
          // 先算成 number | null，再取出一个纯数字给百分比用：
          // 直接在 JSX 里对 ratio 做算术的话，TS 收窄不到"非 null"那个分支。
          const ratio = blank ? null : m.onTarget / m.meals
          const pct = ratio === null ? 0 : Math.round(ratio * 100)
          return (
            <li key={m.name}>
              <button
                type="button"
                className={blank ? 'wk-mem is-none' : 'wk-mem'}
                onClick={() => onOpen(m.name)}
                aria-label={`查看${m.name}这一周的饮食详情`}
              >
                <span className="wk-mem-name">{m.name}</span>
                <span className="wk-mem-cap">本周达标进度</span>
                <span className="wk-mem-mid">
                  <span className="wk-mem-num">
                    {blank ? (
                      <b className="wk-mem-blank">待补</b>
                    ) : (
                      <>
                        <b>{m.onTarget}</b>
                        <i>/{m.meals}</i>
                        <span className="wk-mem-sep">·</span>
                        <b>{pct}%</b>
                      </>
                    )}
                  </span>
                  <RingProgress
                    value={ratio}
                    size={78}
                    title={blank ? `${m.name}这一周还没有记录` : `${m.name}达标 ${pct}%`}
                  />
                </span>
                <span className="wk-mem-prev">{prevText(m)}</span>
                <span className="wk-mem-hint">
                  <i className="wk-mem-hint-k">需要注意</i>
                  <span className="wk-mem-hint-t">{m.hint}</span>
                </span>
                <span className="wk-mem-go" aria-hidden="true">
                  看详情 →
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}



