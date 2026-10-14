import type { RunState } from '../data/model.ts'
import { formatSpoken, stageLabel } from '../data/model.ts'
import { clockOf } from '../data/waitLines.ts'
import { Icon } from '../ui/Icon.tsx'

/**
 * 等待页的「处理详情（已完成的步骤）」折叠行（2026-10-12 照稿重排）。
 *
 * 稿把它画成一条**独立的描边行**（不是卡片内部的一段），收起时只有图标 +
 * 标题 + 右侧 chevron，与下面的「正在生成中…」面板等宽并列。所以它从
 * .wait-actions 里独立出来，取消按钮搬回 WaitCard——出口在版面最下方，
 * 与这一行之间隔着整块正文。
 *
 * 「已完成的步骤」这五个字是**这一行自己的**标题，不是对内容的承诺：
 * 展开后列的每一条都是后端真发过的阶段事件（时刻 + 阶段 + 第几次），
 * 一条不多。后端不会逐步报告它做到哪一步，所以条目少是事实、不是漏了——
 * 那句说明留在展开区里，收起时不占版面。
 */
export function WaitDetail({ run }: { run: RunState }) {
  return (
    <details className="wait-detail">
      <summary>
        <Icon name="doc" />
        <span className="wait-detail-title">处理详情</span>
        <span className="wait-detail-hint">（已完成的步骤）</span>
        {/* chevron 用 CSS 画（一条折线），不用图标：它要随展开态翻转 90°，
            而 Icon 的所有者是"形状一套、不给调用方改形"的那个文件。 */}
        <span className="wait-detail-chev" aria-hidden="true" />
      </summary>

      <div className="wait-detail-inner">
        <p className="wait-detail-time">
          已用时 <span className="mono">{formatSpoken(run.elapsed)}</span>
          {run.heartbeats.length > 0 && <span> · 心跳 {run.heartbeats.length} 次</span>}
        </p>
        {run.events.length === 0 ? (
          <p className="wait-detail-none">至今没有收到任何阶段事件。</p>
        ) : (
          <ol className="wait-log">
            {run.events.map((e, i) => (
              <li key={`${e.at}-${i}`}>
                <span className="mono">{clockOf(e.at)}</span>
                <span>正在{stageLabel(e.stage)}</span>
                {e.nth > 1 && <span className="wait-log-nth">第 {e.nth} 次</span>}
              </li>
            ))}
          </ol>
        )}
        <p className="wait-detail-note">
          后端不会逐步报告它做到哪一步。这里只列它真的发过来过的东西，所以条目少是事实，不是漏了。
        </p>
      </div>
    </details>
  )
}
