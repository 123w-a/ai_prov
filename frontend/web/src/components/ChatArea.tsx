import { useEffect, useRef, useState } from 'react'
import { fetchNearby } from '../api/client'
import type { ChatMessage, DecisionMode, NearbyResult } from '../types'
import { Icon } from './Icon'
import { RecipeCard } from './RecipeCard'

interface Props {
  activeTitle: string
  messages: ChatMessage[]
  sending: boolean
  onSend: (text: string, image: File | null, mode: DecisionMode, imagePreview: string | null) => void
  onClear: () => void
  onDeleteTurn: (messageId: number) => void
  onTranscribe: (audio: File) => Promise<string>
}

const MODES: Array<{
  id: DecisionMode
  label: string
  hint: string
  icon: 'utensils' | 'concierge' | 'image' | 'shield'
}> = [
  { id: 'home', label: '在家做', hint: '用现有食材', icon: 'utensils' },
  { id: 'dining', label: '出去吃', hint: '帮我做选择', icon: 'concierge' },
  { id: 'fridge', label: '看冰箱', hint: '图片或清单', icon: 'image' },
  { id: 'health', label: '健康问答', hint: '先查证再回答', icon: 'shield' },
]

const QUICK_PROMPTS: Array<{ text: string; mode: DecisionMode; tag: string }> = [
  { text: '今晚想吃清淡一点，30 分钟内能做好', mode: 'home', tag: '快手晚餐' },
  { text: '高血压，想吃面，帮我避开高盐做法', mode: 'health', tag: '健康护栏' },
  { text: '冰箱里有鸡蛋、番茄和青椒，做什么合适？', mode: 'fridge', tag: '清理冰箱' },
  { text: '不想做饭，预算 40 元，怎么点更均衡？', mode: 'dining', tag: '懒人点单' },
]

const STAGE_COPY = {
  thinking: '正在理解你的需求',
  writing: '正在形成膳食建议',
  searching: '正在检索做法与营养依据',
  auditing: '正在做健康护栏审计',
  generating_image: '正在生成菜品图片',
  structuring: '正在完成健康审计与卡片整理',
}

type VoiceState = 'idle' | 'recording' | 'transcribing'

export function ChatArea({
  activeTitle,
  messages,
  sending,
  onSend,
  onClear,
  onDeleteTurn,
  onTranscribe,
}: Props) {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<DecisionMode>('home')
  const [image, setImage] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [nearbyResult, setNearbyResult] = useState<NearbyResult | null>(null)
  const [nearbyLoading, setNearbyLoading] = useState(false)
  const [nearbyError, setNearbyError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const recordingStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  const canSend = !sending && voiceState !== 'transcribing' && (text.trim().length > 0 || image)

  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller) return
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: messages.length > 2 ? 'smooth' : 'auto' })
  }, [messages])

  useEffect(
    () => () => {
      const recorder = recorderRef.current
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null
        recorder.stop()
      }
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop())
    },
    [],
  )

  const submit = () => {
    if (!canSend) return
    onSend(text.trim(), image, mode, preview)
    setText('')
    setImage(null)
    setPreview(null)
    setNotice('')
    if (fileRef.current) fileRef.current.value = ''
  }

  const choosePrompt = (prompt: (typeof QUICK_PROMPTS)[number]) => {
    setMode(prompt.mode)
    setText(prompt.text)
    textareaRef.current?.focus()
  }

  const pickImage = (file: File | undefined) => {
    if (!file) return
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
      setNotice('只支持 JPG、PNG、WEBP 图片。')
      return
    }
    setImage(file)
    setNotice('')
    const reader = new FileReader()
    reader.onload = () => setPreview(String(reader.result))
    reader.readAsDataURL(file)
  }

  const finishVoiceRecording = async (mimeType: string, stream: MediaStream) => {
    stream.getTracks().forEach((track) => track.stop())
    recordingStreamRef.current = null
    const blob = new Blob(audioChunksRef.current, { type: mimeType || 'audio/webm' })
    audioChunksRef.current = []
    if (blob.size === 0) {
      setVoiceState('idle')
      setNotice('没有录到声音，请重试。')
      return
    }

    const isM4a = /mp4|m4a/i.test(mimeType)
    const extension = isM4a ? 'm4a' : 'webm'
    const fileType = isM4a ? 'audio/m4a' : mimeType || 'audio/webm'
    const audio = new File([blob], `voice-${Date.now()}.${extension}`, { type: fileType })

    setVoiceState('transcribing')
    setNotice('正在把语音转成文字…')
    try {
      const transcript = await onTranscribe(audio)
      if (!transcript.trim()) throw new Error('没有识别到有效文字')
      setText((current) => (current.trim() ? `${current.trim()} ${transcript.trim()}` : transcript.trim()))
      setNotice('语音已转成文字，可以继续修改后发送。')
      textareaRef.current?.focus()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '语音识别失败，请稍后重试。')
    } finally {
      setVoiceState('idle')
    }
  }

  const startVoiceRecording = async () => {
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setNotice('当前浏览器不支持麦克风录音。')
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const preferredMime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : ''
      const recorder = preferredMime
        ? new MediaRecorder(stream, { mimeType: preferredMime })
        : new MediaRecorder(stream)

      recordingStreamRef.current = stream
      recorderRef.current = recorder
      audioChunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        void finishVoiceRecording(recorder.mimeType || preferredMime || 'audio/webm', stream)
      }
      recorder.start()
      setVoiceState('recording')
      setNotice('正在录音，再次点击麦克风结束。')
    } catch (error) {
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop())
      recordingStreamRef.current = null
      setVoiceState('idle')
      setNotice(error instanceof Error ? `无法使用麦克风：${error.message}` : '无法使用麦克风。')
    }
  }

  const loadNearby = async () => {
    setNearbyLoading(true)
    setNearbyError('')
    try {
      const data = await fetchNearby({ query: text.trim(), budget: 50 })
      setNearbyResult(data)
    } catch (error) {
      setNearbyError(error instanceof Error ? error.message : String(error))
    } finally {
      setNearbyLoading(false)
    }
  }

  const toggleVoice = () => {
    if (voiceState === 'recording') {
      recorderRef.current?.stop()
      return
    }
    if (voiceState === 'idle') void startVoiceRecording()
  }

  return (
    <main className="chat-workspace">
      <header className="conversation-header">
        <div>
          <span className="eyebrow">Active decision</span>
          <h2>{activeTitle || '新的膳食决策'}</h2>
        </div>
        <div className="conversation-actions">
          <span className="memory-badge">
            <Icon name="chat" size={15} />
            {Math.ceil(messages.length / 2)} 轮上下文
          </span>
          <button
            type="button"
            className="text-btn danger"
            disabled={messages.length === 0 || sending}
            onClick={onClear}
          >
            <Icon name="trash" size={16} />
            清空本轮
          </button>
        </div>
      </header>

      <div className="chat-feed" ref={scrollRef}>
        {messages.length === 0 ? (
          <section className="welcome-panel">
            <div className="welcome-copy">
              <span className="welcome-kicker">
                <Icon name="leaf" size={17} />
                今天这顿，先替你排除不合适的
              </span>
              <h1>
                把选择交给我，
                <br />
                你只管<span>好好吃饭。</span>
              </h1>
              <p>
                说说现有食材、预算、口味或健康需求。小膳管家会先查证与校验，再给出一份能落地的方案。
              </p>
            </div>

            <div className="welcome-stamp" aria-hidden="true">
              <span>AI</span>
              <strong>膳食决策</strong>
              <small>SAFE · SIMPLE · TRACEABLE</small>
            </div>

            <div className="capability-strip" aria-label="当前能力">
              <div>
                <Icon name="shield" />
                <span>
                  <strong>健康护栏</strong>
                  慢病与忌口优先
                </span>
              </div>
              <div>
                <Icon name="book" />
                <span>
                  <strong>依据可溯源</strong>
                  展示知识库命中
                </span>
              </div>
              <div>
                <Icon name="image" />
                <span>
                  <strong>图文都能问</strong>
                  支持冰箱与食材图
                </span>
              </div>
            </div>

            <div className="quick-prompts">
              <div className="quick-prompts-head">
                <span>不知道怎么开口？</span>
                <small>点一条继续编辑</small>
              </div>
              <div className="quick-prompt-grid">
                {QUICK_PROMPTS.map((prompt) => (
                  <button
                    type="button"
                    key={prompt.tag}
                    className="quick-prompt"
                    onClick={() => choosePrompt(prompt)}
                  >
                    <span>{prompt.tag}</span>
                    <p>{prompt.text}</p>
                    <b aria-hidden="true">↗</b>
                  </button>
                ))}
              </div>
            </div>
          </section>
        ) : (
          <div className="message-list">
            {messages.map((message) => (
              <article key={message.id} className={`message-row ${message.role}`}>
                <div className="message-avatar" aria-hidden="true">
                  {message.role === 'assistant' ? <Icon name="chef" size={18} /> : '我'}
                </div>
                <div className="message-column">
                  <header className="message-meta">
                    <strong>{message.role === 'assistant' ? '小膳管家' : '你'}</strong>
                    {message.time && <time>{message.time}</time>}
                    {message.role === 'assistant' && message.recordId && (
                      <button
                        type="button"
                        className="message-delete"
                        aria-label="删除这一轮对话"
                        onClick={() => onDeleteTurn(message.recordId!)}
                      >
                        <Icon name="trash" size={14} />
                      </button>
                    )}
                  </header>
                  <div className={`message-content ${message.error ? 'has-error' : ''}`}>
                    {message.imageUrl && (
                      <img className="message-image" src={message.imageUrl} alt="本轮上传的食材图片" />
                    )}
                    {message.text && <div className="message-text">{message.text}</div>}
                    {message.answer && <RecipeCard answer={message.answer} />}
                    {message.streaming && (
                      <div className="stream-state" role="status" aria-live="polite">
                        <span className="stream-dots" aria-hidden="true">
                          <i />
                          <i />
                          <i />
                        </span>
                        {STAGE_COPY[message.stage || 'thinking']}
                      </div>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <footer className="composer-shell">
        <div className="mode-switcher" aria-label="选择决策场景">
          {MODES.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={mode === item.id}
              className={mode === item.id ? 'mode-option active' : 'mode-option'}
              onClick={() => setMode(item.id)}
            >
              <Icon name={item.icon} size={17} />
              <span>
                <strong>{item.label}</strong>
                <small>{item.hint}</small>
              </span>
            </button>
          ))}
        </div>

        {mode === 'dining' && (
          <section className="nearby-panel" aria-label="附近餐厅建议">
            <header className="nearby-panel-head">
              <div>
                <Icon name="concierge" size={18} />
                <span>附近餐厅</span>
              </div>
              <button type="button" onClick={() => void loadNearby()} disabled={nearbyLoading}>
                {nearbyLoading ? '查询中' : '查询附近'}
              </button>
            </header>

            {nearbyError && <p className="nearby-error">{nearbyError}</p>}

            {nearbyResult && (
              <div className="nearby-list">
                {nearbyResult.restaurants.map((restaurant) => (
                  <article key={restaurant.name} className="nearby-card">
                    <div className="nearby-card-title">
                      <strong>{restaurant.name}</strong>
                      <span>{restaurant.cuisine}</span>
                    </div>
                    <div className="nearby-meta">
                      <span>人均 ¥{restaurant.avg_price ?? '?'}</span>
                      {restaurant.distance_km != null && <span>{restaurant.distance_km}km</span>}
                      {restaurant.address && <span>{restaurant.address}</span>}
                    </div>
                    {restaurant.guardrail && <p>{restaurant.guardrail}</p>}
                  </article>
                ))}
              </div>
            )}
          </section>
        )}

        {preview && (
          <div className="attachment-preview">
            <img src={preview} alt="待发送图片预览" />
            <div>
              <strong>{image?.name}</strong>
              <span>发送后会交给视觉模型理解</span>
            </div>
            <button
              type="button"
              className="icon-btn"
              aria-label="移除待发送图片"
              onClick={() => {
                setImage(null)
                setPreview(null)
                if (fileRef.current) fileRef.current.value = ''
              }}
            >
              <Icon name="close" size={18} />
            </button>
          </div>
        )}

        <div className="composer">
          <label htmlFor="meal-request" className="sr-only">
            描述你的食材、口味或健康需求
          </label>
          <textarea
            ref={textareaRef}
            id="meal-request"
            value={text}
            rows={3}
            placeholder="例如：冰箱里有鸡蛋和西兰花，今晚想吃得清淡一点…"
            disabled={sending}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
          />

          <div className="composer-toolbar">
            <div className="composer-tools">
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                hidden
                onChange={(event) => pickImage(event.target.files?.[0])}
              />
              <button
                type="button"
                className="tool-btn"
                disabled={sending}
                onClick={() => fileRef.current?.click()}
              >
                <Icon name="image" size={18} />
                图片
              </button>
              <button
                type="button"
                className={voiceState === 'recording' ? 'tool-btn recording' : 'tool-btn'}
                disabled={sending || voiceState === 'transcribing'}
                aria-pressed={voiceState === 'recording'}
                onClick={toggleVoice}
              >
                <Icon name="mic" size={18} />
                {voiceState === 'recording' ? '结束录音' : voiceState === 'transcribing' ? '识别中' : '语音'}
              </button>
            </div>

            <div className="send-zone">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <button type="button" className="send-btn" disabled={!canSend} onClick={submit}>
                <span>{sending ? '正在决策' : '开始决策'}</span>
                <Icon name="send" size={18} />
              </button>
            </div>
          </div>
        </div>

        <div className="composer-status" role="status" aria-live="polite">
          {notice || '建议只作膳食参考；涉及疾病治疗与用药，请咨询专业医生。'}
        </div>
      </footer>
    </main>
  )
}
