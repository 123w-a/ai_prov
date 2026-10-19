import { readRunError } from '../data/runError.ts'

/**
 * 一轮没跑完时的那块说明（首屏顶部）。
 *
 * 改这里的两条理由，都来自 2026-10-19 那次真实故障：
 *  ① 原来直接把后端 `run.error` 印出来，而那句话是上游代理的英文原文
 *     （`Our servers are currently overloaded...`）。用户读完不知道要干什么。
 *     现在主句是中文、说清"谁的锅 + 你该做什么"，原文收进折叠里供核对。
 *  ② 原来失败后没有任何出口——输入框里的原话还在，但按钮已随状态复位。
 *     现在给「再试一次」，用 run.request（这一轮真正发出去的正文）重发，
 *     不用重新打字，也不会因为输入框被改过而发错内容。
 *
 * 纪律：这块**只用 run 里已有的事实**（error / httpStatus / body / request），
 * 不猜原因、不编"请稍后再试"以外的承诺；没有原文就不显示折叠。
 */
export function FailureNote({ run, onRetry }: {
  run: {
    error: string | null
    httpStatus?: number
    body?: string
    request?: string
  }
  /** 原话重发。不传时只显示说明与原文（夹具、只读场景）。 */
  onRetry?: () => void
}) {
  const view = readRunError(run.error)
  const canRetry = view.retryable && !!run.request && !!onRetry

  return (
    <div className="failure" role="status">
      <strong>{view.title}</strong>
      {view.hint && <p className="failure-hint">{view.hint}</p>}
      {run.httpStatus !== undefined && <p className="mono">HTTP {run.httpStatus}</p>}

      {canRetry && (
        <div className="failure-actions">
          <button type="button" className="failure-retry" onClick={onRetry}>
            再试一次
          </button>
          <span className="failure-what">重发：{run.request}</span>
        </div>
      )}

      {/* 原文恒留、默认收起：它是核对用的证据，不是给用户的第一句话。 */}
      {view.detail && (
        <details className="failure-partial">
          <summary>通道返回的原话（技术细节）</summary>
          <pre>{view.detail}</pre>
        </details>
      )}

      {run.body && (
        <details className="failure-partial">
          <summary>查看已经收到的部分内容（可能不完整）</summary>
          <pre>{run.body}</pre>
        </details>
      )}
    </div>
  )
}
