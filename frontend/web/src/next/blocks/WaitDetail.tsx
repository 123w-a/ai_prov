import type { RunState } from '../data/model.ts'
import { formatSpoken, stageLabel } from '../data/model.ts'
import { clockOf } from '../data/waitLines.ts'

/**
 * 等待页的「处理详情」折叠 + 动作按钮（2026-10-01 从 WaitCard 拆出）。
 *
 * 顺序照设计稿：先给"详情"入口，再给按钮。读完了再决定要不要动作。
 * 详情只列后端真的发过来的东西——条目少是事实，不是漏了（lave 契约：不把
 * 查证→核对→整理 画成三步进度，后端会重复推这些阶段）。
 */
export function WaitDetail({
  run,
  onCancel,
  onRestart,
}: {
  run: RunState
  onCancel?: () => void
  onRestart?: () => void
}) {
  return (
    <div className="wait-actions">
      <details className="wait-detail">
        <summary>处理详情</summary>
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
      </details>

      {run.status === 'running' && !run.orphaned && onCancel && (
        <button className="act act-quiet" type="button" onClick={onCancel}>
          取消本次请求
        </button>
      )}
      {run.orphaned && onRestart && (
        <button className="act act-quiet" type="button" onClick={onRestart}>
          重新开始
        </button>
      )}
    </div>
  )
}
