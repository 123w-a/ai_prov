import { useEffect, useRef, useState } from 'react'
import { fetchNearby, sendMessageFeedback } from '../api/client'
import type { ChatMessage, DecisionMode, NearbyResult } from '../types'
import { Icon } from './Icon'
import html2canvas from 'html2canvas'
import { RecipeCard } from './RecipeCard'
import { renderRichText } from '../utils/richText'

interface Props {
  activeTitle: string
  activeSessionId: string | null
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
  switching_model: '主模型超时，已切换备用模型重试',
}

// T1 即时状态打卡：一次性生效，随下次发送注入消息前缀并自动清空
const STATUS_TAGS = ['昨晚没睡好', '今天肌肉酸痛', '肠胃不太舒服', '很累没力气'] as const

type VoiceState = 'idle' | 'recording' | 'transcribing'

export function ChatArea({
  activeTitle,
  activeSessionId,
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
  const [statusTags, setStatusTags] = useState<string[]>([])
  const [fbState, setFbState] = useState<Record<number, 'up' | 'down' | null>>({})

  const rateAnswer = async (recordId: number, rating: 'up' | 'down') => {
    if (!activeSessionId) return
    setFbState((s) => ({ ...s, [recordId]: rating })) // 乐观更新
    try {
      const result = await sendMessageFeedback(activeSessionId, recordId, rating)
      setFbState((s) => ({ ...s, [recordId]: result })) // 同值再点=取消，服务端返回 null
    } catch {
      // 失败回滚到未标记：下次点击重试
      setFbState((s) => ({ ...s, [recordId]: null }))
    }
  }
  // 浏览器定位（附近餐厅用）：拿不到就静默降级，不阻塞输入
  const [coords, setCoords] = useState('')
  const [locating, setLocating] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [report, setReport] = useState<{ has_data: boolean; message?: string; meals?: number; top_dishes?: [string, number][]; lights?: Record<string, number>; light_trends?: Record<string, 'improving' | 'worsening' | 'stable' | 'insufficient'>; guardrail_triggers?: number; range?: [string, string]; next_week_shopping?: string[]; recommendations?: string[] } | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
  const [shopDishes, setShopDishes] = useState('')
  const [shopInv, setShopInv] = useState('')
  const [shopResult, setShopResult] = useState<{ matched_dishes: string[]; unknown_dishes: string[]; main: string[]; seasoning: string[]; sections?: Array<{ name: string; items: string[] }> } | null>(null)
  const [shopOpen, setShopOpen] = useState(false)
  const [fridgeItems, setFridgeItems] = useState<string[]>([])
  const [shopNotice, setShopNotice] = useState('')
  const [savingFridge, setSavingFridge] = useState(false)
  const [visionBusy, setVisionBusy] = useState(false)
  const [visionDraft, setVisionDraft] = useState<Array<{ name: string; quantity: string }> | null>(null)
  const visionInputRef = useRef<HTMLInputElement>(null)
  const loadShopping = async () => {
    setShopOpen(true)
    try {
      const fridgeRes = await fetch('/api/fridge')
      if (!fridgeRes.ok) throw new Error('fridge request failed')
      const fridge = await fridgeRes.json() as { items?: string[] }
      const savedInventory = fridge.items || []
      setFridgeItems(savedInventory)
      const inventory = [...savedInventory, ...shopInv.split(',').map((item) => item.trim()).filter(Boolean)]
      const res = await fetch(`/api/shopping/list?dishes=${encodeURIComponent(shopDishes)}&inventory=${encodeURIComponent(inventory.join(','))}`)
      if (!res.ok) throw new Error('shopping request failed')
      setShopResult(await res.json())
      setSelectedItems([])
      setShopNotice('清单已按当前冰箱库存更新。')
    } catch {
      setShopResult(null)
      setShopNotice('清单加载失败，请稍后重试。')
    }
  }
  const loadReport = async () => {
    setReportOpen(true)
    try {
      const res = await fetch('/api/reports/weekly')
      setReport(await res.json())
    } catch {
      setReport({ has_data: false, message: '周报加载失败，请稍后再试。' })
    }
  }
  useEffect(() => {
    if (!('geolocation' in navigator)) return
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        setCoords(
          `${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`,
        ),
      () => {},
      { timeout: 8000 },
    )
  }, [])
  const [nearbyResult, setNearbyResult] = useState<NearbyResult | null>(null)
  const [nearbyLoading, setNearbyLoading] = useState(false)
  const [nearbyError, setNearbyError] = useState('')
  const [selectedItems, setSelectedItems] = useState<string[]>([])
  const reportRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const toggleItem = (item: string) => setSelectedItems((p) => (p.includes(item) ? p.filter((i) => i !== item) : [...p, item]))
  const exportReport = async () => {
    if (!reportRef.current) return
    const canvas = await html2canvas(reportRef.current, { scale: 2 })
    const url = canvas.toDataURL('image/png')
    const a = document.createElement('a')
    a.href = url
    a.download = `饮食周报_${new Date().toISOString().slice(0, 10)}.png`
    a.click()
  }
  const exportReportPdf = async () => {
    if (!reportRef.current) return
    const [{ jsPDF }, canvas] = await Promise.all([import('jspdf'), html2canvas(reportRef.current, { scale: 2 })])
    const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })
    const width = 190
    const height = Math.min((canvas.height * width) / canvas.width, 277)
    pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 10, 10, width, height)
    pdf.save(`饮食周报_${new Date().toISOString().slice(0, 10)}.pdf`)
  }
  const pickFridgePhoto = async (file: File) => {
    if (visionBusy) return
    setVisionBusy(true)
    setShopNotice('正在识别照片中的食材…')
    try {
      const body = new FormData()
      body.append('image', file)
      const res = await fetch('/api/fridge/vision', { method: 'POST', body })
      if (!res.ok) {
        const detail = await res.json().catch(() => null) as { detail?: string } | null
        throw new Error(detail?.detail || 'vision failed')
      }
      const payload = await res.json() as { items: Array<{ name: string; quantity: string }> }
      if (!payload.items.length) {
        setShopNotice('照片里没有识别出食材，请换一张更清晰的照片。')
        return
      }
      setVisionDraft(payload.items)
      setShopNotice('识别完成，请确认或删改后写入冰箱。')
    } catch (exc) {
      setShopNotice(`识别失败：${exc instanceof Error ? exc.message : '请稍后重试'}`)
    } finally {
      setVisionBusy(false)
    }
  }
  const confirmVisionDraft = async () => {
    if (!visionDraft?.length || savingFridge) return
    setSavingFridge(true)
    try {
      const names = visionDraft.map((item) => item.name)
      const res = await fetch('/api/fridge/add', { method: 'POST', body: new URLSearchParams({ items: names.join(',') }) })
      if (!res.ok) throw new Error('fridge save failed')
      const saved = await res.json() as { items?: string[] }
      setFridgeItems(saved.items || [])
      setVisionDraft(null)
      setShopNotice(`已把识别的 ${names.length} 项写入冰箱。`)
    } catch {
      setShopNotice('写入失败，请检查后端服务后重试。')
    } finally {
      setSavingFridge(false)
    }
  }
  const saveFridge = async () => {
    if (!selectedItems.length || savingFridge) return
    setSavingFridge(true)
    try {
      const res = await fetch('/api/fridge/add', { method: 'POST', body: new URLSearchParams({ items: selectedItems.join(',') }) })
      if (!res.ok) throw new Error('fridge save failed')
      const saved = await res.json() as { items?: string[] }
      const savedCount = selectedItems.length
      setFridgeItems(saved.items || [])
      setSelectedItems([])
      setShopNotice(`已保存 ${savedCount} 项到冰箱，当前库存已刷新。`)
    } catch {
      setShopNotice('保存失败，请检查后端服务后重试。')
    } finally {
      setSavingFridge(false)
    }
  }
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
    // 即时状态打卡（一次性）+ 当前位置（外食查询用）前缀注入
    const parts: string[] = []
    if (statusTags.length > 0) parts.push(`[实时状态：${statusTags.join('、')}]
`)
    if (coords) parts.push(`[当前位置：${coords}]
`)
    const prefix = parts.join('')
    onSend(`${prefix}${text.trim()}`, image, mode, preview)
    setText('')
    setStatusTags([])
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
                    {message.role === 'assistant' && message.recordId && !message.streaming && (
                      <span className="msg-feedback">
                        <button
                          type="button"
                          className={(fbState[message.recordId] ?? message.feedback ?? null) === 'up' ? 'fb-btn active' : 'fb-btn'}
                          aria-label="这个回答有帮助"
                          title="有帮助"
                          onClick={() => void rateAnswer(message.recordId!, 'up')}
                        >
                          <Icon name="thumb-up" size={13} />
                        </button>
                        <button
                          type="button"
                          className={(fbState[message.recordId] ?? message.feedback ?? null) === 'down' ? 'fb-btn active' : 'fb-btn'}
                          aria-label="这个回答不满意"
                          title="不满意"
                          onClick={() => void rateAnswer(message.recordId!, 'down')}
                        >
                          <Icon name="thumb-down" size={13} />
                        </button>
                      </span>
                    )}
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
                    {message.text && <div className="message-text">{renderRichText(message.text)}</div>}
                    {message.answer && <RecipeCard answer={message.answer} />}
                    {message.streaming && (
                      <div className="stream-state" role="status" aria-live="polite">
                        <span className="stream-dots" aria-hidden="true">
                          <i />
                          <i />
                          <i />
                        </span>
                        {STAGE_COPY[message.stage || 'thinking']}
                        {message.elapsed != null && message.elapsed >= 15 && message.stage !== 'writing' && message.stage !== 'structuring' && (` · 已等待 ${message.elapsed}s（上游模型响应较慢）`)}
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

        {shopOpen && mode === 'fridge' && (
          <section className="nearby-panel report-card" aria-label="采购清单">
            <header className="nearby-panel-head">
              <div><Icon name="concierge" size={18} /><span>采购清单</span></div>
              <button type="button" aria-label="关闭清单" onClick={() => setShopOpen(false)}>×</button>
            </header>
            <input
              value={shopDishes}
              onChange={(e) => setShopDishes(e.target.value)}
              placeholder="想吃的菜，逗号分隔：番茄炒蛋,青椒肉丝"
              style={{ width: '100%', marginBottom: 6 }}
            />
            <input
              value={shopInv}
              onChange={(e) => setShopInv(e.target.value)}
              placeholder="家里已有的（可选）：鸡蛋,盐"
              style={{ width: '100%', marginBottom: 8 }}
            />
            <button type="button" className="tool-btn" onClick={() => void loadShopping()} disabled={!shopDishes.trim()}>
              {shopResult ? '重新生成' : '生成清单'}
            </button>
            {shopResult && (
              <div className="nearby-list shopping-result" style={{ marginTop: 10 }}>
                <div className="shopping-ingredients owned-ingredients">
                  <strong>已拥有（冰箱 {fridgeItems.length} 项）：</strong>
                  {fridgeItems.length > 0 ? fridgeItems.map((ing) => <span key={ing} className="ingredient-chip owned-chip">{ing}</span>) : <span className="shopping-empty">暂无已记录库存</span>}
                </div>
                <div className="vision-row">
                  <input
                    ref={visionInputRef}
                    type="file"
                    accept="image/*"
                    capture="environment"
                    style={{ display: 'none' }}
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) void pickFridgePhoto(f); e.currentTarget.value = '' }}
                    aria-label="选择冰箱照片"
                  />
                  <button
                    type="button"
                    className="tool-btn"
                    disabled={visionBusy}
                    onClick={() => visionInputRef.current?.click()}
                  >
                    {visionBusy ? '识别中…' : '📷 拍照清点冰箱'}
                  </button>
                  <span className="shopping-empty">拍一张冰箱内部照，AI 列出食材草稿</span>
                </div>
                {visionDraft && visionDraft.length > 0 && (
                  <div className="shopping-ingredients vision-draft">
                    <strong>识别草稿：</strong>
                    {visionDraft.map((item, index) => (
                      <span key={`${item.name}-${index}`} className="ingredient-chip vision-chip">
                        {item.name}{item.quantity ? ` ${item.quantity}` : ''}
                        <button
                          type="button"
                          aria-label={`删除 ${item.name}`}
                          onClick={() => setVisionDraft(visionDraft.filter((_, i) => i !== index))}
                        >×</button>
                      </span>
                    ))}
                    <button type="button" className="tool-btn" disabled={savingFridge} onClick={() => void confirmVisionDraft()}>
                      {savingFridge ? '写入中…' : '确认写入冰箱'}
                    </button>
                  </div>
                )}
                {(shopResult.main.length > 0 || shopResult.seasoning.length > 0) && <strong className="shopping-section-title">待购买</strong>}
                {(shopResult.sections || []).length > 0 ? shopResult.sections!.map((sec) => (
                  <div key={sec.name} className="shopping-ingredients">
                    <strong>{sec.name}：</strong>
                    {sec.items.map((ing) => (
                      <label key={ing} className="ingredient-chip"><input type="checkbox" checked={selectedItems.includes(ing)} onChange={() => toggleItem(ing)} /><span>{ing}</span></label>
                    ))}
                  </div>
                )) : (
                  <>
                    {shopResult.main.length > 0 && (
                      <div className="shopping-ingredients">
                        <strong>主料：</strong>
                        {shopResult.main.map((ing) => (
                          <label key={ing} className="ingredient-chip"><input type="checkbox" checked={selectedItems.includes(ing)} onChange={() => toggleItem(ing)} /><span>{ing}</span></label>
                        ))}
                      </div>
                    )}
                    {shopResult.seasoning.length > 0 && (
                      <div className="shopping-ingredients"><strong>调味料：</strong>{shopResult.seasoning.map((ing) => (
                        <label key={ing} className="ingredient-chip"><input type="checkbox" checked={selectedItems.includes(ing)} onChange={() => toggleItem(ing)} /><span>{ing}</span></label>
                      ))}</div>
                    )}
                  </>
                )}
                {shopResult.main.length === 0 && shopResult.seasoning.length === 0 && (
                  <p className="shopping-empty">当前库存已满足这份清单，无需采购。</p>
                )}
                {shopResult.unknown_dishes.length > 0 && (
                  <p style={{ opacity: 0.7 }}>未收录菜谱：{shopResult.unknown_dishes.join('、')}</p>
                )}
                <button type="button" className="tool-btn" style={{ marginTop: 8, width: '100%' }} disabled={!selectedItems.length || savingFridge} onClick={() => void saveFridge()}>{savingFridge ? '保存中…' : '保存选中到冰箱'}</button>
                {shopNotice && <p className="shop-notice" role="status">{shopNotice}</p>}
              </div>
            )}
          </section>
        )}

        {reportOpen && (
          <section className="nearby-panel report-card" aria-label="本周周报" ref={reportRef}>
            <header className="nearby-panel-head">
              <div>
                <Icon name="concierge" size={18} />
                <span>本周饮食周报</span>
              </div>
              <div className="report-actions">
                <button type="button" aria-label="导出周报图片" onClick={() => void exportReport()}>📤</button>
                <button type="button" aria-label="导出周报PDF" onClick={() => void exportReportPdf()}>📄</button>
                <button type="button" aria-label="关闭周报" onClick={() => setReportOpen(false)}>×</button>
              </div>
            </header>
            {!report ? (
              <p>加载中…</p>
            ) : !report.has_data ? (
              <p>{report.message}</p>
            ) : (
              <div className="report-body">
                <div className="report-hero">
                  <div className="hero-num">{report.meals}<span>餐</span></div>
                  <div className="hero-sub">近 7 天饮食决策 · 护栏触发 {report.guardrail_triggers} 次</div>
                </div>
                {(report.top_dishes || []).length > 0 && (
                  <div className="report-block">
                    <div className="block-label">常吃 TOP</div>
                    <div className="dish-chips">
                      {(report.top_dishes as [string, number][]).map(([d, n]) => (
                        <span key={d} className="dish-chip">{d}{n > 1 && <em>×{n}</em>}</span>
                      ))}
                    </div>
                  </div>
                )}
                {Object.keys(report.lights || {}).length > 0 && (
                  <div className="report-block">
                    <div className="block-label">红绿灯合计</div>
                    <div className="dish-chips">
                      {Object.entries(report.lights || {}).map(([k, v]) => {
                        const idx = k.lastIndexOf(':')
                        const name = idx >= 0 ? k.slice(0, idx) : k
                        const color = idx >= 0 ? k.slice(idx + 1) : 'green'
                        return <span key={k} className={`light-pill light-${color}`}>{name} ×{v as number}</span>
                      })}
                    </div>
                  </div>
                )}
                {Object.keys(report.light_trends || {}).length > 0 && (
                  <div className="report-block">
                    <div className="block-label">风险趋势 <small>近 3 天 vs 前 4 天</small></div>
                    <div className="dish-chips">
                      {Object.entries(report.light_trends || {}).map(([name, trend]) => {
                        const label = trend === 'improving' ? '改善' : trend === 'worsening' ? '需关注' : trend === 'stable' ? '持平' : '数据不足'
                        return <span key={name} className={`trend-pill trend-${trend}`}>{name} · {label}</span>
                      })}
                    </div>
                  </div>
                )}
                {(report.recommendations || []).length > 0 && (
                  <div className="report-block">
                    <div className="block-label">小膳建议</div>
                    <ul className="recommendation-list">
                      {(report.recommendations as string[]).map((tip) => (
                        <li key={tip}>{tip}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {report.range && (
                  <div className="report-range">{report.range[0]} ~ {report.range[1]}</div>
                )}
                {(report.next_week_shopping || []).length > 0 && (
                  <div className="report-shopping">
                    <div className="block-label">下周购物清单</div>
                    <div className="dish-chips">
                      {(report.next_week_shopping || []).map((ing: string) => (
                        <label key={ing} className="ingredient-chip">
                          <input type="checkbox" checked={selectedItems.includes(ing)} onChange={() => toggleItem(ing)} />
                          <span>{ing}</span>
                        </label>
                      ))}
                    </div>
                    <button type="button" className="tool-btn" style={{ marginTop: 8, width: '100%' }} disabled={!selectedItems.length} onClick={() => void saveFridge()}>
                      保存选中到冰箱
                    </button>
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {mode === 'dining' && panelOpen && (
          <section className="nearby-panel" aria-label="附近餐厅建议">
            <header className="nearby-panel-head">
              <div>
                <Icon name="concierge" size={18} />
                <span>附近餐厅</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button type="button" onClick={() => void loadNearby()} disabled={nearbyLoading}>
                  {nearbyLoading ? '查询中' : '查询附近'}
                </button>
                <button type="button" aria-label="关闭面板" onClick={() => setPanelOpen(false)}>×</button>
              </div>
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

          <div className="composer-tools" role="group" aria-label="实时状态打卡">
            <button
              type="button"
              className={coords ? 'tool-btn' : 'tool-btn mood-active'}
              disabled={locating}
              title={coords ? `已定位：${coords}` : '点击重新请求浏览器定位授权（附近餐厅需要）'}
              onClick={() => {
                if (!('geolocation' in navigator)) return
                setLocating(true)
                navigator.geolocation.getCurrentPosition(
                  (pos) => {
                    setCoords(`${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`)
                    setLocating(false)
                  },
                  () => setLocating(false),
                  { timeout: 8000 },
                )
              }}
            >
              {locating ? '定位中…' : coords ? '📍 已定位' : '📍 未定位·点此授权'}
            </button>
            {mode === 'dining' && !panelOpen && (
              <button type="button" className="tool-btn" onClick={() => setPanelOpen(true)}>
                🍽 附近餐厅
              </button>
            )}
            <button type="button" className="tool-btn" onClick={() => void loadReport()}>
              📊 本周周报
            </button>
            {mode === 'fridge' && (
              <button type="button" className="tool-btn" onClick={() => setShopOpen(!shopOpen)}>
                🛒 采购清单
              </button>
            )}
            {STATUS_TAGS.map((tag) => {
              const active = statusTags.includes(tag)
              return (
                <button
                  key={tag}
                  type="button"
                  className={active ? 'tool-btn mood-active' : 'tool-btn'}
                  disabled={sending}
                  aria-pressed={active}
                  onClick={() =>
                    setStatusTags((prev) =>
                      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
                    )
                  }
                >
                  {active ? `✓ ${tag}` : tag}
                </button>
              )
            })}
          </div>

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
