import { useState } from 'react'
import type { RailSession } from '../data/railTime.ts'
import { fmtTime, titleOf } from '../data/railTime.ts'

/**
 * 会话列表的一行（2026-10-19 从旧前端 SessionSidebar 找回删除/改名）。
 *
 * 交互逐条对齐旧前端：
 *   · 改名是**行内输入框**（点「改名」把标题换成输入框，Enter/失焦提交，Esc 取消）；
 *   · 空名不保存、原样退出（旧 commitRename 同判据）；
 *   · 删除只发意图，确认框与善后（删的是当前会话怎么办）都在 app 层。
 * 编辑状态收在本行内：每一行各自管自己的草稿，父组件不养 editingId/draft
 * 两份状态（那两份在旧实现里就要互相盯着同步）。
 *
 * 单独成文件是给 checkScale 让路：blocks 单文件 ≤150 行，塞回 SessionRail 会越线。
 */

export function SessionRow({
  s,
  active,
  onPick,
  onRename,
  onDelete,
}: {
  s: RailSession
  active: boolean
  onPick: (sid: string) => void
  onRename: (sid: string, title: string) => void
  onDelete: (sid: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const name = titleOf(s)

  const start = () => {
    setDraft(s.title || '新对话')
    setEditing(true)
  }
  const commit = () => {
    const title = draft.trim()
    setEditing(false)
    // 空名不保存（旧实现同款）：不是"清空标题"，是"没改"。
    if (!title) return
    onRename(s.session_id, title)
  }

  return (
    <li className="sr-li">
      {editing ? (
        <input
          className="sr-rename"
          aria-label={`重命名会话：${name}`}
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <button type="button" className="sr-item" data-on={active} data-sid={s.session_id} onClick={() => onPick(s.session_id)}>
          <span className="sr-title">{name}</span>
          <span className="sr-time">{fmtTime(s)}</span>
        </button>
      )}
      <button type="button" className="sr-act" aria-label={`重命名会话：${name}`} disabled={editing} onClick={start}>
        改名
      </button>
      <button type="button" className="sr-act is-del" aria-label={`删除会话：${name}`} onClick={() => onDelete(s.session_id)}>
        删除
      </button>
    </li>
  )
}
