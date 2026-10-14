/**
 * C 设计稿的卡 4「护栏提示」与卡 6「可执行建议」。
 *
 * 两张卡的关系是"问题"与"行动"：卡 4 说这周哪里需要注意（禁忌护栏触发了几次、
 * 哪些营养素没到绿灯），卡 6 给出去做什么（后端已算好的建议）。
 *
 * 卡 4 的三行素材**全部来自后端已给的真数据**，前端只做筛选与排序：
 *   · 护栏触发次数 ← `guardrail_triggers`；
 *   · "哪几顿没到绿灯" ← 逐餐 `lights`（黄 + 红 的计数）；
 *   · 反馈 ← 已有的 feedback_summary。
 * 这里**不推断**"蔬菜吃少了""全谷物不足"这类判断——记账行里没有食材表，
 * 那种话要么来自后端建议，要么就不说（本项目铁律：不许补造）。
 *
 * 一条灯都没黄没红时，卡 4 不硬凑一句话，就写「这几项本周都到了绿灯」。
 * 那是真结论，不是安慰。
 */
import { Icon } from '../ui/Icon'
import { CardHead } from './WeekCardShell'
import type { LightCounts } from '../data/firstScreen.ts'

export function WeekGuardCard({
  guardrail,
  lights,
  feedback,
}: {
  guardrail: number
  /** `LightCounts` 本身就是「营养素列表」这个数组类型，不是单个元素的类型。 */
  lights: LightCounts
  feedback: { count: number; tags: Record<string, number> } | null
}) {
  // 没到绿灯的项排在前面：卡 4 的用途就是"先看哪里要改"。
  const off = lights
    .filter((l) => l.yellow + l.red > 0)
    .sort((a, b) => b.yellow + b.red - (a.yellow + a.red))
  const fbTags = feedback ? Object.entries(feedback.tags) : []
  return (
    <section className="wk-card is-guard">
      <CardHead n="4" title="护栏提示" />
      <ul className="wk-bullets">
        <li>
          {guardrail > 0
            ? `本周触发 ${guardrail} 次饮食禁忌护栏（已避开或已给替换）`
            : '本周没有触发饮食禁忌护栏'}
        </li>
        {off.map((l) => (
          <li key={l.nutrient}>
            {l.nutrient}
            {l.red > 0 ? ` 有 ${l.red} 顿红灯` : ''}
            {l.red > 0 && l.yellow > 0 ? '、' : ''}
            {l.yellow > 0 ? `${l.red > 0 ? '' : ' 有 '}${l.yellow} 顿基本达标` : ''}
            <span className="wk-bullet-tail">，还没到绿灯</span>
          </li>
        ))}
        {off.length === 0 && lights.length > 0 ? (
          <li>这几项营养本周都到了绿灯</li>
        ) : null}
      </ul>
      {feedback ? (
        <div className="wk-card-fb">
          <p className="wk-card-fb-k">本周反馈 · {feedback.count} 条</p>
          {fbTags.length > 0 ? (
            <ul className="wk-tags">
              {fbTags.map(([tag, n]) => (
                <li key={tag}>
                  {tag}
                  <b>{n}</b>
                </li>
              ))}
            </ul>
          ) : (
            <p className="wk-fb-empty">
              <Icon name="chat" />
              <span>还没有收到反馈</span>
            </p>
          )}
        </div>
      ) : null}
    </section>
  )
}

export function WeekAdviceCard({ recommendations }: { recommendations: string[] }) {
  if (recommendations.length === 0) return null
  return (
    <section className="wk-card is-advice">
      <CardHead n="6" title="可执行建议" />
      <ul className="wk-list">
        {recommendations.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </section>
  )
}

