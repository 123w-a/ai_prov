import { useMemo, useState } from 'react'
import type { Session, WorkspaceView } from '../types'
import { FamilyPanel } from './FamilyPanel'
import { Icon } from './Icon'

interface Props {
  sessions: Session[]
  activeId: string | null
  view: WorkspaceView
  open: boolean
  connection: 'checking' | 'online' | 'offline'
  onViewChange: (view: WorkspaceView) => void
  onSelect: (session: Session) => void
  onRename: (sessionId: string, title: string) => void
  onNew: () => void
  onDelete: (sessionId: string) => void
  onClose: () => void
}

export function SessionSidebar({
  sessions,
  activeId,
  view,
  open,
  connection,
  onViewChange,
  onSelect,
  onRename,
  onNew,
  onDelete,
  onClose,
}: Props) {
  const [query, setQuery] = useState('')
  const [familyOpen, setFamilyOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const visibleSessions = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    if (!keyword) return sessions
    return sessions.filter((session) =>
      (session.title || '新对话').toLocaleLowerCase().includes(keyword),
    )
  }, [query, sessions])

  const changeView = (nextView: WorkspaceView) => {
    onViewChange(nextView)
    onClose()
  }

  const selectSession = (session: Session) => {
    onSelect(session)
    onViewChange('decision')
    onClose()
  }

  const startRename = (session: Session) => {
    setEditingId(session.session_id)
    setEditTitle(session.title || '新对话')
  }

  const commitRename = (session: Session) => {
    const title = editTitle.trim()
    if (!title) {
      setEditingId(null)
      return
    }
    onRename(session.session_id, title)
    setEditingId(null)
  }

  return (
    <aside className={`sidebar ${open ? 'is-open' : ''}`} aria-label="主导航与历史会话">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <Icon name="chef" size={25} />
        </div>
        <div>
          <strong>小膳管家</strong>
          <span>AI meal steward</span>
        </div>
        <button type="button" className="icon-btn sidebar-close" onClick={onClose} aria-label="关闭导航">
          <Icon name="close" />
        </button>
      </div>

      <nav className="product-nav" aria-label="产品功能">
        <button
          type="button"
          className={view === 'decision' ? 'product-nav-item active' : 'product-nav-item'}
          onClick={() => changeView('decision')}
        >
          <span className="nav-icon">
            <Icon name="spark" />
          </span>
          <span>
            <strong>膳食决策</strong>
            <small>菜谱 · 护栏 · 依据</small>
          </span>
        </button>
        <button
          type="button"
          className={view === 'service' ? 'product-nav-item active' : 'product-nav-item'}
          onClick={() => changeView('service')}
        >
          <span className="nav-icon">
            <Icon name="concierge" />
          </span>
          <span>
            <strong>私厨预演</strong>
            <small>食材缺口计算</small>
          </span>
          <span className="nav-tag">DEMO</span>
        </button>
        <button
          type="button"
          className={familyOpen ? 'product-nav-item active' : 'product-nav-item'}
          onClick={() => setFamilyOpen(true)}
        >
          <span className="nav-icon">
            <Icon name="book" />
          </span>
          <span>
            <strong>家庭成员</strong>
            <small>画像 · 切换 · 分享</small>
          </span>
        </button>
        <button
          type="button"
          className={view === 'weekly' ? 'product-nav-item active' : 'product-nav-item'}
          onClick={() => changeView('weekly')}
        >
          <span className="nav-icon">
            <Icon name="leaf" />
          </span>
          <span>
            <strong>本周周报</strong>
            <small>红绿灯 · 趋势 · 建议</small>
          </span>
        </button>
        <button
          type="button"
          className={view === 'favorites' ? 'product-nav-item active' : 'product-nav-item'}
          onClick={() => changeView('favorites')}
        >
          <span className="nav-icon">
            <Icon name="star" />
          </span>
          <span>
            <strong>我的收藏</strong>
            <small>好菜留存 · 随时复做</small>
          </span>
        </button>
      </nav>

      {familyOpen ? (
        <FamilyPanel onBack={() => setFamilyOpen(false)} />
      ) : (

      <section className="history-section" aria-labelledby="history-heading">
        <div className="sidebar-section-head">
          <div>
            <span className="eyebrow">历史记录</span>
            <h2 id="history-heading">最近决策</h2>
          </div>
          <button type="button" className="icon-btn new-session-btn" onClick={onNew} aria-label="新建会话">
            <Icon name="plus" />
          </button>
        </div>

        <label className="session-search">
          <span className="sr-only">搜索历史会话</span>
          <Icon name="search" size={17} />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索会话"
          />
        </label>

        <ul className="session-list">
          {visibleSessions.map((session) => {
            const active = session.session_id === activeId
            const messageCount = session.messages?.length ?? 0
            return (
              <li key={session.session_id} className={active ? 'session-item active' : 'session-item'}>
                <button
                  type="button"
                  className="session-main"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => selectSession(session)}
                >
                  <span className="session-symbol" aria-hidden="true">
                    <Icon name="chat" size={16} />
                  </span>
                  <span className="session-copy">
                    {editingId === session.session_id ? (
                      <input
                        autoFocus
                        value={editTitle}
                        className="session-title-input"
                        onChange={(event) => setEditTitle(event.target.value)}
                        onBlur={() => commitRename(session)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') commitRename(session)
                          if (event.key === 'Escape') setEditingId(null)
                        }}
                      />
                    ) : (
                      <span className="session-title">{session.title || '新对话'}</span>
                    )}
                    <span className="session-meta">
                      {session.created_at || '刚刚'} · {messageCount} 轮
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className="session-rename"
                  aria-label={`重命名会话：${session.title || '新对话'}`}
                  onClick={() => startRename(session)}
                >
                  <Icon name="pencil" size={15} />
                </button>
                <button
                  type="button"
                  className="session-delete"
                  aria-label={`删除会话：${session.title || '新对话'}`}
                  onClick={() => onDelete(session.session_id)}
                >
                  <Icon name="trash" size={16} />
                </button>
              </li>
            )
          })}
        </ul>

        {visibleSessions.length === 0 && (
          <div className="sidebar-empty">
            <Icon name="chat" />
            <p>{query ? '没有匹配的会话' : '还没有历史记录'}</p>
            {!query && (
              <button type="button" onClick={onNew}>
                开始第一次决策
              </button>
            )}
          </div>
        )}
        </section>
      )}

      <footer className="sidebar-footer">
        <span className={`connection-dot ${connection}`} aria-hidden="true" />
        <span>
          {connection === 'online' ? '决策服务已连接' : connection === 'offline' ? '服务暂未连接' : '正在连接服务'}
        </span>
        <small>FastAPI · SSE</small>
      </footer>
    </aside>
  )
}
