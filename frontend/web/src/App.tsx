import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  clearSession,
  createSession,
  deleteMessage,
  deleteSession,
  fetchSessions,
  sendChat,
  transcribeAudio,
} from './api/client'
import { ChatArea } from './components/ChatArea'
import { Icon } from './components/Icon'
import { InsightPanel } from './components/InsightPanel'
import { ServicePreview } from './components/ServicePreview'
import { SessionSidebar } from './components/SessionSidebar'
// P1: sidebar hosts FamilyPanel (member switcher + profiles)
import type {
  ChatMessage,
  ChefAnswer,
  DecisionMode,
  Session,
  WorkspaceView,
} from './types'

// 场景前缀由后端按 mode 处理，前端只传原始文本与 mode。

function parseHistoryAnswer(raw: string | undefined): ChefAnswer | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && Array.isArray((parsed as ChefAnswer).recipes)) {
      return parsed as ChefAnswer
    }
  } catch {
    // 历史数据可能是旧版纯文本回答。
  }
  return null
}

function sessionToMessages(session: Session): ChatMessage[] {
  const history: ChatMessage[] = []
  for (const record of session.messages ?? []) {
    history.push({
      id: `user-${session.session_id}-${record.id}`,
      recordId: record.id,
      role: 'user',
      text: record.user_text || (record.image_url ? '上传了一张食材图片' : '（图片）'),
      imageUrl: record.image_url,
      time: record.time,
    })
    const answer = parseHistoryAnswer(record.answer)
    history.push({
      id: `assistant-${session.session_id}-${record.id}`,
      recordId: record.id,
      role: 'assistant',
      text: answer ? '' : (record.answer === '__pending__' ? '（回答生成中，请稍后刷新查看…）' : record.answer || ''),
      answer,
      time: record.time,
    })
  }
  return history
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [view, setView] = useState<WorkspaceView>('decision')
  const [sending, setSending] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [connection, setConnection] = useState<'checking' | 'online' | 'offline'>('checking')
  const [appError, setAppError] = useState('')
  const bootRef = useRef(false)

  const selectSession = useCallback((session: Session) => {
    setActiveId(session.session_id)
    setMessages(sessionToMessages(session))
  }, [])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await fetchSessions()
      setSessions(list)
      setConnection('online')
      setAppError('')
      return list
    } catch (error) {
      setConnection('offline')
      throw error
    }
  }, [])

  const syncActiveSession = useCallback(
    async (sessionId: string) => {
      const list = await refreshSessions()
      const current = list.find((session) => session.session_id === sessionId)
      if (current) selectSession(current)
      return list
    },
    [refreshSessions, selectSession],
  )

  const handleNew = useCallback(async () => {
    try {
      const session = await createSession()
      setSessions((current) => [session, ...current])
      selectSession(session)
      setView('decision')
      setSidebarOpen(false)
      setConnection('online')
      setAppError('')
    } catch (error) {
      setConnection('offline')
      setAppError(`新建会话失败：${errorMessage(error)}`)
    }
  }, [selectSession])

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      if (!window.confirm('确认删除这个会话？其中的历史记录将无法恢复。')) return
      try {
        await deleteSession(sessionId)
        const list = await refreshSessions()
        if (activeId !== sessionId) return

        if (list.length > 0) {
          selectSession(list[0])
        } else {
          const freshSession = await createSession()
          setSessions([freshSession])
          selectSession(freshSession)
        }
      } catch (error) {
        setAppError(`删除会话失败：${errorMessage(error)}`)
      }
    },
    [activeId, refreshSessions, selectSession],
  )

  const handleClearSession = useCallback(async () => {
    if (!activeId || messages.length === 0) return
    if (!window.confirm('清空当前会话中的全部问答？会话本身会保留。')) return
    try {
      await clearSession(activeId)
      await syncActiveSession(activeId)
    } catch (error) {
      setAppError(`清空会话失败：${errorMessage(error)}`)
    }
  }, [activeId, messages.length, syncActiveSession])

  const handleDeleteTurn = useCallback(
    async (messageId: number) => {
      if (!activeId) return
      if (!window.confirm('删除这一轮问答？')) return
      try {
        await deleteMessage(activeId, messageId)
        await syncActiveSession(activeId)
      } catch (error) {
        setAppError(`删除问答失败：${errorMessage(error)}`)
      }
    },
    [activeId, syncActiveSession],
  )

  const handleSend = useCallback(
    async (
      text: string,
      image: File | null,
      mode: DecisionMode,
      imagePreview: string | null,
    ) => {
      if (sending) return

      let sessionId = activeId
      if (!sessionId) {
        try {
          const freshSession = await createSession()
          setSessions((current) => [freshSession, ...current])
          setActiveId(freshSession.session_id)
          sessionId = freshSession.session_id
        } catch (error) {
          setAppError(`无法开始对话：${errorMessage(error)}`)
          return
        }
      }

      const userId = crypto.randomUUID()
      const assistantId = crypto.randomUUID()
      const displayText = text || '请根据这张图片帮我做膳食决策'
      const requestText = displayText

      setMessages((current) => [
        ...current,
        {
          id: userId,
          role: 'user',
          text: displayText,
          imageUrl: imagePreview,
        },
        {
          id: assistantId,
          role: 'assistant',
          text: '',
          streaming: true,
          stage: 'thinking',
        },
      ])
      setSending(true)
      setAppError('')

      try {
        await sendChat(sessionId, requestText, image, mode, {
          onStage: (stage) =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, stage: stage as ChatMessage["stage"] } : message,
              ),
            ),
          onWorking: () =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, stage: 'thinking' } : message,
              ),
            ),
          onToken: (token) =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, text: message.text + token, stage: 'writing' }
                  : message,
              ),
            ),
          onStructuring: () =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, stage: 'structuring' } : message,
              ),
            ),
          onHeartbeat: (elapsed) =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId ? { ...message, elapsed } : message,
              ),
            ),
          onAnswer: (answer) =>
            setMessages((current) =>
              current.map((message) =>
                message.id === assistantId
                  ? { ...message, answer, streaming: false, stage: undefined }
                  : message,
              ),
            ),
          onImage: (img) =>
            setMessages((current) =>
              current.map((message) => {
                if (message.id !== assistantId || !message.answer) return message
                const recipes = (message.answer.recipes ?? []).map((recipe, index) =>
                  index === img.index
                    ? { ...recipe, image_url: img.url, image_ai_generated: img.ai_generated }
                    : recipe,
                )
                const updated: ChefAnswer = { ...message.answer, recipes }
                if (img.index === 0) {
                  updated.image_url = img.url
                  updated.image_ai_generated = img.ai_generated
                }
                return { ...message, answer: updated }
              }),
            ),
        })
        await syncActiveSession(sessionId)
      } catch (error) {
        const detail = errorMessage(error)
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  text: `${message.text}${message.text ? '\n\n' : ''}本轮未能完成：${detail}`,
                  streaming: false,
                  stage: undefined,
                  error: true,
                }
              : message,
          ),
        )
        setAppError(`膳食决策未完成：${detail}`)
      } finally {
        setSending(false)
      }
    },
    [activeId, sending, syncActiveSession],
  )

  useEffect(() => {
    if (bootRef.current) return
    bootRef.current = true
    void (async () => {
      try {
        const list = await refreshSessions()
        if (list.length > 0) {
          selectSession(list[0])
        } else {
          const session = await createSession()
          setSessions([session])
          selectSession(session)
          setConnection('online')
        }
      } catch (error) {
        setConnection('offline')
        setAppError(`无法连接后端服务：${errorMessage(error)}`)
      }
    })()
  }, [refreshSessions, selectSession])

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeId) ?? null,
    [activeId, sessions],
  )

  const latestAnswer = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].answer) return messages[index].answer ?? null
    }
    return null
  }, [messages])

  const pageTitle = view === 'decision' ? '今日膳食决策' : '上门私厨预演'
  const pageDescription =
    view === 'decision' ? '健康优先的 AI 膳食工作台' : '查看真实可用能力与远期服务边界'

  return (
    <div className="app-shell">
      <SessionSidebar
        sessions={sessions}
        activeId={activeId}
        view={view}
        open={sidebarOpen}
        connection={connection}
        onViewChange={setView}
        onSelect={selectSession}
        onNew={() => void handleNew()}
        onDelete={(sessionId) => void handleDeleteSession(sessionId)}
        onClose={() => setSidebarOpen(false)}
      />

      <button
        type="button"
        className={sidebarOpen ? 'sidebar-overlay show' : 'sidebar-overlay'}
        aria-label="关闭导航"
        onClick={() => setSidebarOpen(false)}
      />

      <div className="main-shell">
        <header className="global-header">
          <button
            type="button"
            className="icon-btn mobile-menu"
            aria-label="打开导航"
            onClick={() => setSidebarOpen(true)}
          >
            <Icon name="menu" />
          </button>
          <div className="global-title">
            <span>{pageDescription}</span>
            <h1>{pageTitle}</h1>
          </div>
          <div className="global-status">
            <span className={`status-chip ${connection}`}>
              <i aria-hidden="true" />
              {connection === 'online' ? '服务在线' : connection === 'offline' ? '连接异常' : '连接中'}
            </span>
            <span className="date-chip">健康链透明可追溯</span>
          </div>
        </header>

        {appError && (
          <div className="app-alert" role="alert">
            <Icon name="warning" size={18} />
            <span>{appError}</span>
            <button type="button" className="icon-btn" aria-label="关闭错误提示" onClick={() => setAppError('')}>
              <Icon name="close" size={17} />
            </button>
          </div>
        )}

        {view === 'decision' ? (
          <div className="decision-layout">
            <ChatArea
              activeTitle={activeSession?.title || '新的膳食决策'}
              activeSessionId={activeSession?.session_id ?? null}
              messages={messages}
              sending={sending}
              onSend={(text, image, mode, preview) => void handleSend(text, image, mode, preview)}
              onClear={() => void handleClearSession()}
              onDeleteTurn={(messageId) => void handleDeleteTurn(messageId)}
              onTranscribe={transcribeAudio}
            />
            <InsightPanel answer={latestAnswer} />
          </div>
        ) : (
          <ServicePreview />
        )}
      </div>
    </div>
  )
}
