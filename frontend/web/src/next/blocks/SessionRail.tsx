import type { RailSession } from '../data/railTime.ts'
import { fmtTime, lastMs, msgCount, titleOf } from '../data/railTime.ts'

/**
 * 历史会话侧栏（2026-10-01 B 批次③，lave gpt-5.6-sol 方案 A）。
 *
 * 归属（lave 裁决原样保留）：只在今晚房出现、只读 fetchSessions()、
 * 展开收起不改房间 hash、首切片只给标题+最后时间（预览会把私人对话
 * 内容钉在列表上，那是隐私面不是功能面）。纯函数与结构类型在
 * data/railTime.ts（e 门禁：blocks 不引后端类型）。
 *
 * 对 lave 的一处偏差（2026-10-01 实现时定）：lave 说「常驻可收起左栏」，
 * 这里做成「标题行常驻按钮 + 左侧浮层面板」——面板 fixed、带遮罩，完全
 * 不推挤主区。理由：1010px 骨架刚过用户验收，常驻左栏会改掉每页宽度；
 * 对一个排版被点名批评过的产品，先保零回归。常驻可达与稳定浏览位置
 * 两条诉求都满足。
 *
 * 空会话分组（43/58 是空「新对话」）：不分组真实内容会被一模一样的
 * 「新对话」淹掉，等于不可用；分组不丢数据——收在 <details> 里照样可点。
 */

function Row({
  s,
  active,
  onPick,
}: {
  s: RailSession
  active: boolean
  onPick: (sid: string) => void
}) {
  return (
    <li>
      <button
        type="button"
        className="rail-item"
        data-on={active}
        data-sid={s.session_id}
        onClick={() => onPick(s.session_id)}
      >
        <span className="rail-title">{titleOf(s)}</span>
        <span className="rail-time">{fmtTime(s)}</span>
        {active && <span className="rail-on">当前</span>}
      </button>
    </li>
  )
}

export function SessionRail({
  open,
  sessions,
  activeId,
  notice,
  loading,
  error,
  onPick,
  onClose,
}: {
  open: boolean
  sessions: RailSession[]
  activeId: string | null
  notice: string | null
  loading: boolean
  error: string | null
  onPick: (sid: string) => void
  onClose: () => void
}) {
  if (!open) return null

  // 不依赖后端返回顺序：按最后活跃时间自己倒序。
  const byLast = (a: RailSession, b: RailSession) => lastMs(b) - lastMs(a)
  const withMsg = sessions.filter((s) => msgCount(s) > 0).sort(byLast)
  const empty = sessions.filter((s) => msgCount(s) === 0).sort(byLast)

  return (
    <div className="rail-mask" onClick={onClose}>
      <aside
        className="rail"
        role="dialog"
        aria-modal="true"
        aria-label="历史会话"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="rail-head">
          <h2 className="rail-h">历史会话</h2>
          <button type="button" className="rail-close" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="rail-sub">
          点一条，下一条消息就写进它。正在跑的那轮会被取消，并且会明说。
        </p>

        {notice && (
          <p className="rail-notice" role="status">
            {notice}
          </p>
        )}
        {loading && <p className="rail-note">正在读会话列表…</p>}
        {error && (
          <p className="rail-err" role="alert">
            {error}
          </p>
        )}
        {!loading && !error && sessions.length === 0 && (
          <p className="rail-note">还没有任何会话。</p>
        )}

        {withMsg.length > 0 && (
          <ul className="rail-list">
            {withMsg.map((s) => (
              <Row key={s.session_id} s={s} active={s.session_id === activeId} onPick={onPick} />
            ))}
          </ul>
        )}

        {empty.length > 0 && (
          <details className="rail-bucket">
            <summary>空会话（{empty.length}）</summary>
            <ul className="rail-list">
              {empty.map((s) => (
                <Row
                  key={s.session_id}
                  s={s}
                  active={s.session_id === activeId}
                  onPick={onPick}
                />
              ))}
            </ul>
          </details>
        )}
      </aside>
    </div>
  )
}
