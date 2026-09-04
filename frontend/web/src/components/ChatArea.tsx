import { useEffect, useRef, useState } from 'react'
import { fetchNearby, resolveLocation, sendMessageFeedback } from '../api/client'
import type { ChatMessage, DecisionMode, NearbyResult, ResolvedLocation } from '../types'
import { Icon } from './Icon'
import html2canvas from 'html2canvas'
import { starMessage as starMessageApi, addDislike, fetchTasteSuggestion, addTasteNote } from '../api/client'
import type { TasteSuggestion } from '../api/client'
import { RecipeCard } from './RecipeCard'
import { renderRichText } from '../utils/richText'

interface Props {
  activeTitle: string
  activeSessionId: string | null
  messages: ChatMessage[]
  sending: boolean
  onSend: (
    text: string,
    image: File | null,
    mode: DecisionMode,
    imagePreview: string | null,
    wantImage: boolean,
    locationContext?: string | null,
  ) => void
  onCancelImageDecision: () => Promise<boolean>
  onRenameTitle: (title: string) => void
  onClear: () => void
  onDeleteTurn: (messageId: number) => void
  onTranscribe: (audio: File) => Promise<string>
}

const QUICK_PROMPTS: Array<{ text: string; mode: DecisionMode; tag: string }> = [
  { text: '今晚想吃清淡一点，30 分钟内能做好', mode: 'home', tag: '快手晚餐' },
  { text: '高血压，想吃面，帮我避开高盐做法', mode: 'health', tag: '健康护栏' },
  { text: '冰箱里有鸡蛋、番茄和青椒，做什么合适？', mode: 'home', tag: '清理冰箱' },
]

const STAGE_COPY = {
  thinking: '小膳思考中',
  writing: '正在形成膳食建议',
  searching: '正在检索做法与营养依据',
  auditing: '正在做健康护栏审计',
  generating_image: '正在生成菜品图片',
  structuring: '正在完成健康审计与卡片整理',
  switching_model: '主模型超时，已切换备用模型重试',
}

const STRONG_IMAGE_REQUESTS = ['配图', '配张图', '补图', '换图', '换张图', '生成图片', '生成一张图', '来张图', '发图', '发张图', '发图片', '发个图', '出图', '出个图', '看看图', '看看图片', '看图片', '看图', '看一下图', '看一下图片', '看个图', '给我看图', '给我看看', '让我看看', '想看图片', '想看图', '图片欣赏', '成品图', '成品照', '实拍图', '示意图', '效果图', '样图', '参考图', '想看看', '长什么样', '什么样子', '啥样', '样式', '外观', '照片', '实拍', '再来一张', '换一张', '另一张', '重新生成']

const hasStrongImageRequest = (value: string) => STRONG_IMAGE_REQUESTS.some((phrase) => value.includes(phrase))

// T1 即时状态打卡：一次性生效，随下次发送注入消息前缀并自动清空
const STATUS_TAGS = ['昨晚没睡好', '今天肌肉酸痛', '肠胃不太舒服', '很累没力气'] as const

type VoiceState = 'idle' | 'recording' | 'transcribing'

export function ChatArea({
  activeTitle,
  activeSessionId,
  messages,
  sending,
  onSend,
  onCancelImageDecision,
  onRenameTitle,
  onClear,
  onDeleteTurn,
  onTranscribe,
}: Props) {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<DecisionMode>('home')
  const [wantImage, setWantImage] = useState(false)
  const [image, setImage] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [statusTags, setStatusTags] = useState<string[]>([])
  const [fbState, setFbState] = useState<Record<number, 'up' | 'down' | null>>({})
  const [starState, setStarState] = useState<Record<number, boolean>>({})
  const [dislikeHint, setDislikeHint] = useState<string | null>(null)
  const [tasteHint, setTasteHint] = useState<TasteSuggestion | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const [coords, setCoords] = useState('')
  const [locationInfo, setLocationInfo] = useState<ResolvedLocation | null>(null)
  const [locating, setLocating] = useState(false)
  const [locationPressed, setLocationPressed] = useState(false)

  const starMessage = async (recordId: number) => {
    if (!activeSessionId) return
    const next = !(starState[recordId] ?? false)
    setStarState((s) => ({ ...s, [recordId]: next })) // 乐观更新
    try {
      const result = await starMessageApi(activeSessionId, recordId, next)
      setStarState((s) => ({ ...s, [recordId]: result.starred }))
    } catch {
      setStarState((s) => ({ ...s, [recordId]: !next })) // 失败回滚
    }
  }

  const rateAnswer = async (recordId: number, rating: 'up' | 'down') => {
    if (!activeSessionId) return
    setFbState((s) => ({ ...s, [recordId]: rating })) // 乐观更新
    try {
      const result = await sendMessageFeedback(activeSessionId, recordId, rating)
      setFbState((s) => ({ ...s, [recordId]: result })) // 同值再点=取消，服务端返回 null
      if (result === 'down') {
        // 点踩后查口味建议：同口味被踩≥2次才提示（用户确认式沉淀，零幻觉）
        fetchTasteSuggestion()
          .then((sug) => {
            if (sug) setTasteHint(sug)
          })
          .catch(() => {})
      }
    } catch {
      // 失败回滚到未标记：下次点击重试
      setFbState((s) => ({ ...s, [recordId]: null }))
    }
  }
  const [panelOpen, setPanelOpen] = useState(false)
  const [report, setReport] = useState<{ has_data: boolean; message?: string; meals?: number; top_dishes?: [string, number][]; lights?: Record<string, number>; light_trends?: Record<string, 'improving' | 'worsening' | 'stable' | 'insufficient'>; guardrail_triggers?: number; range?: [string, string]; recommendations?: string[] } | null>(null)
  const [reportOpen, setReportOpen] = useState(false)
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
    const savedCoords = window.localStorage.getItem('xiaoshan-coords') || ''
    if (savedCoords) {
      setCoords(savedCoords)
      void syncCoords(savedCoords)
    }
  }, [])

  useEffect(() => {
    if (!('geolocation' in navigator)) {
      setNotice('当前浏览器不支持定位。')
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => void syncCoords(`${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`),
      () => {},
      { timeout: 12000, enableHighAccuracy: true, maximumAge: 0 },
    )
  }, [])
  const [nearbyResult, setNearbyResult] = useState<NearbyResult | null>(null)
  const [nearbyLoading, setNearbyLoading] = useState(false)
  const [nearbyError, setNearbyError] = useState('')
  const [nearbyPage, setNearbyPage] = useState(1)
  const [nearbyBudget, setNearbyBudget] = useState(50)
  const [nearbyRadius, setNearbyRadius] = useState(1500)
  const [nearbySort, setNearbySort] = useState<'distance' | 'price'>('distance')
  const lastNearbyKeyRef = useRef('')
  const reportRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const syncCoords = async (value: string, announce = false) => {
    setCoords(value)
    window.localStorage.setItem('xiaoshan-coords', value)
    window.dispatchEvent(new CustomEvent('xiaoshan-coords-change', { detail: value }))
    try {
      const resolved = await resolveLocation(value)
      setLocationInfo(resolved)
      if (announce) {
        setNotice(
          resolved.resolved && resolved.label
            ? `附近餐厅 已定位【${resolved.label}】`
            : resolved.warning || resolved.label || '定位解析失败，请手动选择城市',
        )
      }
      return resolved
    } catch {
      const fallback = {
        resolved: false,
        location: value,
        city: '',
        district: '',
        label: '定位解析失败，请手动选择城市',
        warning: '定位解析失败，请手动选择城市',
      }
      setLocationInfo(fallback)
      if (announce) setNotice(fallback.warning)
      return fallback
    }
  }
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
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const recordingStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const stickToBottomRef = useRef(true)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const [liveElapsed, setLiveElapsed] = useState(0)

  const canSend = !sending && voiceState !== 'transcribing' && (text.trim().length > 0 || image)
  const canCancelImageDecision = messages.some(
    (message) =>
      message.role === 'assistant' && (message.streaming || message.imagePending),
  )

  useEffect(() => {
    const scroller = scrollRef.current
    if (!scroller) return
    if (stickToBottomRef.current) {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: messages.length > 2 ? 'smooth' : 'auto' })
    }
  }, [messages])

  useEffect(() => {
    if (!sending) {
      setLiveElapsed(0)
      return
    }
    setLiveElapsed(0)
    const timer = window.setInterval(() => setLiveElapsed((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [sending])

  const handleFeedScroll = () => {
    const scroller = scrollRef.current
    if (!scroller) return
    const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80
    stickToBottomRef.current = nearBottom
    setShowScrollBtn(!nearBottom)
  }

  const scrollToBottom = () => {
    const scroller = scrollRef.current
    if (!scroller) return
    stickToBottomRef.current = true
    setShowScrollBtn(false)
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
  }

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
    const trimmedText = text.trim()
    const explicitImageRequested = hasStrongImageRequest(trimmedText)
    const turnWantsImage = wantImage || explicitImageRequested
    // 忌口语义检测：随口说的「不吃/别放/过敏 X」提示一键沉淀进画像（用户确认式，防误检）
    const dislikeMatch = trimmedText.match(/(?:不吃|别放|不要放|讨厌|过敏)[，, ]?([\u4e00-\u9fa5]{1,4})/)
    if (dislikeMatch) setDislikeHint(dislikeMatch[1])
    // 即时状态打卡（一次性）；定位坐标走 API 入参，不再混进聊天文本
    const parts: string[] = []
    if (statusTags.length > 0) parts.push(`[实时状态：${statusTags.join('、')}]
`)
    const prefix = parts.join('')
    stickToBottomRef.current = true
    setShowScrollBtn(false)
    const locationContext = (() => {
      const lines: string[] = []
      if (coords) lines.push(`GPS坐标：${coords}`)
      if (locationResolved) {
        const area = [locationInfo?.city, locationInfo?.district].filter(Boolean).join(' · ') || locationDetail
        lines.push(`解析位置：${area}`)
      } else if (locationInfo?.label) {
        lines.push(`位置提示：${locationInfo.label}`)
      }
      return lines.length > 0 ? lines.join('\n') : null
    })()
    onSend(`${prefix}${trimmedText}`, image, mode, preview, turnWantsImage, locationContext)
    setText('')
    setStatusTags([])
    setImage(null)
    setPreview(null)
      setWantImage(false)
      setNotice('')
      if (fileRef.current) fileRef.current.value = ''
    }

  const cancelImageDecision = async () => {
    const cancelled = await onCancelImageDecision()
    if (cancelled) setNotice('已取消当前决策')
  }

  const choosePrompt = (prompt: (typeof QUICK_PROMPTS)[number]) => {
    setMode(prompt.mode)
    setWantImage(false)
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

  const buildNearbyKey = (locationValue = coords, budgetValue = nearbyBudget, radiusValue = nearbyRadius) =>
    [locationValue || '', locationInfo?.city || '', locationInfo?.district || '', budgetValue, radiusValue].join('|')

  const loadNearby = async (page = 1, budget = nearbyBudget, locationValue?: string, radiusValue = nearbyRadius) => {
    const activeLocation = locationValue || coords || undefined
    lastNearbyKeyRef.current = buildNearbyKey(activeLocation, budget, radiusValue)
    setNearbyLoading(true)
    setNearbyError('')
    try {
      const data = await fetchNearby({
        city: locationInfo?.city || undefined,
        district: locationInfo?.district || undefined,
        budget,
        location: activeLocation,
        radius: radiusValue,
        page,
      })
      const sorted = [...data.restaurants]
      if (nearbySort === 'distance') {
        sorted.sort((a, b) => (a.distance_km ?? 999) - (b.distance_km ?? 999))
      } else {
        sorted.sort((a, b) => (a.avg_price ?? 999) - (b.avg_price ?? 999))
      }
      data.restaurants = sorted
      setNearbyResult(data)
      setNearbyError(data.warning || '')
      setNearbyPage(page)
    } catch (error) {
      setNearbyError(error instanceof Error ? error.message : String(error))
    } finally {
      setNearbyLoading(false)
    }
  }

  const requestLocation = () => {
    if (!('geolocation' in navigator)) return
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const nextCoords = `${pos.coords.longitude.toFixed(6)},${pos.coords.latitude.toFixed(6)}`
        void syncCoords(nextCoords, true)
          .then((resolved) => {
            if (resolved.resolved) {
              setNearbyResult(null)
              void loadNearby(1, nearbyBudget, nextCoords, nearbyRadius)
            }
          })
          .finally(() => setLocating(false))
      },
      () => {
        setLocating(false)
        setNotice('定位获取失败，请检查浏览器权限')
      },
      { timeout: 12000, enableHighAccuracy: true, maximumAge: 0 },
    )
  }

  const toggleVoice = () => {
    if (voiceState === 'recording') {
      recorderRef.current?.stop()
      return
    }
    if (voiceState === 'idle') void startVoiceRecording()
  }

  const locationResolved = Boolean(locationInfo?.resolved && locationInfo.label)
  const locationStatus = locating ? 'locating' : locationResolved ? 'resolved' : coords ? 'gps' : 'denied'
  const locationDetail = locationResolved
    ? locationInfo!.label
    : locationInfo?.warning || locationInfo?.label || (coords ? '已获取 GPS 坐标' : '未获取定位')
  const locationButtonText = locating
    ? '定位中…'
    : locationPressed && locationResolved
      ? locationDetail
      : locationResolved
      ? '📍 已定位'
      : coords
        ? '📍 已获 GPS'
        : '📍 定位'
  const locationButtonTitle = locationPressed
    ? locationResolved
      ? `附近餐厅 已定位【${locationDetail}】`
      : locationDetail
    : locationResolved
      ? `长按查看城市/区名：${locationDetail}`
      : coords
        ? '已获取 GPS 坐标，正在等待逆地理解析'
        : '点击请求浏览器定位授权'

  useEffect(() => {
    if (!panelOpen || nearbyLoading) return
    if (!coords && !locationResolved) return
    const nextKey = buildNearbyKey(coords || undefined, nearbyBudget, nearbyRadius)
    if (lastNearbyKeyRef.current === nextKey) return
    void loadNearby(1, nearbyBudget, coords || undefined, nearbyRadius)
  }, [panelOpen, coords, locationResolved, locationInfo?.city, locationInfo?.district, nearbyLoading, nearbyBudget, nearbyRadius])

  return (
    <main className="chat-workspace">
      <header className="conversation-header">
        <div>
          <span className="eyebrow">Active decision</span>
          {editingTitle ? (
            <input
              autoFocus
              className="title-editor"
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              onBlur={() => {
                const next = draftTitle.trim()
                if (next) onRenameTitle(next)
                setEditingTitle(false)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  const next = draftTitle.trim()
                  if (next) onRenameTitle(next)
                  setEditingTitle(false)
                }
                if (event.key === 'Escape') setEditingTitle(false)
              }}
            />
          ) : (
            <h2>{activeTitle || '新的膳食决策'}</h2>
          )}
          <button
            type="button"
            className="title-edit-btn"
            aria-label="编辑会话标题"
            onClick={() => {
              setDraftTitle(activeTitle || '新的膳食决策')
              setEditingTitle(true)
            }}
          >
            <Icon name="pencil" size={14} />
          </button>
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

      <div className="chat-feed" ref={scrollRef} onScroll={handleFeedScroll}>
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
                    {message.role === 'assistant' && <strong>小膳管家</strong>}
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
                        <button
                          type="button"
                          className={starState[message.recordId] || message.starred ? 'fb-btn starred' : 'fb-btn'}
                          aria-label="收藏这道菜"
                          title="收藏"
                          onClick={() => void starMessage(message.recordId!)}
                        >
                          <Icon name="star" size={13} />
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
                    {message.text && (message.streaming || message.imagePending || !message.answer || message.error) && (
                      <div className="message-text">{renderRichText(message.text)}</div>
                    )}
                    {message.answer && !message.imagePending && <RecipeCard answer={message.answer} />}
                    {(message.streaming || message.imagePending) && (
                      <div className="stream-state" role="status" aria-live="polite">
                        <span className="stream-dots" aria-hidden="true">
                          <i />
                          <i />
                          <i />
                        </span>
                        {STAGE_COPY[message.imagePending ? 'generating_image' : message.stage || 'thinking']}
                        {liveElapsed >= 15 && message.stage !== 'writing' && message.stage !== 'structuring' && (` · 已等待 ${liveElapsed}s`)}
                        {(message.streaming || message.imagePending) && (
                          <button
                            type="button"
                            className="cancel-decision-inline"
                            onClick={() => void cancelImageDecision()}
                          >
                            取消决策
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
        {showScrollBtn && (
          <button type="button" className="scroll-bottom-btn" aria-label="回到底部" onClick={scrollToBottom}>
            ↓
          </button>
        )}
      </div>

      <footer className="composer-shell">
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
              </div>
            )}
          </section>
        )}

        {panelOpen && (
          <section className="nearby-panel" aria-label="附近餐厅建议">
            <header className="nearby-panel-head">
              <div>
                <Icon name="concierge" size={18} />
                <span>附近餐厅</span>
                <span
                  className={`location-pill ${locationStatus}`}
                  onPointerDown={() => setLocationPressed(true)}
                  onPointerUp={() => setLocationPressed(false)}
                  onPointerCancel={() => setLocationPressed(false)}
                  onPointerLeave={() => setLocationPressed(false)}
                  title={locationButtonTitle}
                >
                  {locationPressed && locationResolved ? locationDetail : locating ? '定位中' : locationResolved ? '已定位' : coords ? 'GPS 已获' : '未授权'}
                </span>
              </div>
              <button type="button" aria-label="关闭面板" onClick={() => setPanelOpen(false)}>×</button>
            </header>

            {nearbyError && <p className="nearby-error">{nearbyError}</p>}

            {!nearbyResult ? (
              <p className="nearby-empty">
                {nearbyLoading
                  ? '正在拉取附近餐厅…'
                  : coords || locationResolved
                    ? '已拿到定位，点击上方预算或半径后会刷新附近餐厅'
                    : '先点定位，再看附近餐厅会更准'}
              </p>
            ) : (
              <>
                <div className="nearby-controls">
                  <div className="budget-chips" aria-label="预算筛选">
                    <span>预算</span>
                    {[30, 50, 0].map((budget) => (
                      <button
                        type="button"
                        key={budget}
                        className={nearbyBudget === budget ? 'budget-chip active' : 'budget-chip'}
                        onClick={() => {
                          setNearbyBudget(budget)
                          void loadNearby(1, budget, undefined, nearbyRadius)
                        }}
                      >
                        {budget === 0 ? '不限' : `¥${budget}`}
                      </button>
                    ))}
                  </div>
                  <div className="budget-chips" aria-label="搜索半径">
                    <span>半径</span>
                    {[800, 1500, 3000].map((radius) => (
                      <button
                        type="button"
                        key={radius}
                        className={nearbyRadius === radius ? 'budget-chip active' : 'budget-chip'}
                        onClick={() => {
                          setNearbyRadius(radius)
                          void loadNearby(1, nearbyBudget, undefined, radius)
                        }}
                      >
                        {radius}m
                      </button>
                    ))}
                  </div>
                  <div className="sort-control">
                    <button
                      type="button"
                      className={nearbySort === 'distance' ? 'sort-btn active' : 'sort-btn'}
                      onClick={() => setNearbySort('distance')}
                    >
                      距离优先
                    </button>
                    <button
                      type="button"
                      className={nearbySort === 'price' ? 'sort-btn active' : 'sort-btn'}
                      onClick={() => setNearbySort('price')}
                    >
                      价格优先
                    </button>
                  </div>
                </div>
                <p className="nearby-hint">
                  {locationResolved
                    ? `以【${locationDetail}】为中心，半径 ${nearbyRadius} 米`
                    : coords
                      ? '已拿到 GPS 坐标，正在等待城市解析'
                      : '先定位后再搜附近餐厅，结果会更准'}
                </p>
                <div className="nearby-list">
                  {nearbyResult.restaurants.map((restaurant) => (
                    <article key={restaurant.name} className="nearby-card">
                      <div className="nearby-card-title">
                        <strong>{restaurant.name}</strong>
                        <span>{restaurant.cuisine}</span>
                      </div>
                      <div className="nearby-meta">
                        <span>人均 ¥{restaurant.avg_price ?? '?'}</span>
                        {restaurant.distance_km != null ? <span>{restaurant.distance_km}km</span> : <span>距离—</span>}
                        {restaurant.address && <span>{restaurant.address}</span>}
                      </div>
                      {restaurant.guardrail && <p>{restaurant.guardrail}</p>}
                    </article>
                  ))}
                </div>
                <div className="nearby-footer">
                  <span className="nearby-source">
                    {nearbyResult.source === 'amap' ? '数据来源：高德地图 · 实时' : '数据来源：离线演示数据 · 非实时推荐'}
                  </span>
                    <button
                      type="button"
                      className="refresh-btn"
                      onClick={() => void loadNearby(nearbyPage + 1, nearbyBudget, undefined, nearbyRadius)}
                      disabled={nearbyLoading}
                    >
                    换一批
                  </button>
                </div>
              </>
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
              className={`tool-btn location-btn ${locationStatus}`}
              disabled={locating}
              title={locationButtonTitle}
              onPointerDown={() => setLocationPressed(true)}
              onPointerUp={() => setLocationPressed(false)}
              onPointerCancel={() => setLocationPressed(false)}
              onPointerLeave={() => setLocationPressed(false)}
              onTouchStart={() => setLocationPressed(true)}
              onTouchEnd={() => setLocationPressed(false)}
              onClick={requestLocation}
            >
              {locationButtonText}
            </button>
            <button
              type="button"
              className={panelOpen ? 'tool-btn mood-active' : 'tool-btn'}
              onClick={() => setPanelOpen((open) => !open)}
            >
              <Icon name="concierge" size={16} />
              {panelOpen ? '收起附近' : '附近餐厅'}
            </button>
            <button type="button" className="tool-btn" onClick={() => void loadReport()}>
              📊 本周周报
            </button>
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
              {sending && canCancelImageDecision && (
                <button type="button" className="cancel-decision-btn" onClick={() => void cancelImageDecision()}>
                  取消决策
                </button>
              )}
              <button type="button" className="send-btn" disabled={!canSend} onClick={submit}>
                <span>{sending ? '正在决策' : '开始决策'}</span>
                <Icon name="send" size={18} />
              </button>
            </div>
          </div>
        </div>

        {dislikeHint && (
          <div className="dislike-hint" role="status">
            <span>
              检测到你提到不吃「<strong>{dislikeHint}</strong>」——要加入画像忌口吗？之后每次推荐都会自动避开。
            </span>
            <span className="dislike-hint-actions">
              <button
                type="button"
                onClick={() => {
                  void addDislike(dislikeHint)
                  setDislikeHint(null)
                }}
              >
                加入画像
              </button>
              <button type="button" className="ghost" onClick={() => setDislikeHint(null)}>
                忽略
              </button>
            </span>
          </div>
        )}

        {tasteHint && (
          <div className="dislike-hint" role="status">
            <span>
              最近 <strong>{tasteHint.count}</strong> 次不满意都和「<strong>{tasteHint.taste}</strong>」有关——
              要把「<strong>{tasteHint.note_label}</strong>」写入画像口味偏好吗？
            </span>
            <span className="dislike-hint-actions">
              <button
                type="button"
                onClick={() => {
                  void addTasteNote(tasteHint.note_label)
                  setTasteHint(null)
                }}
              >
                写入画像
              </button>
              <button type="button" className="ghost" onClick={() => setTasteHint(null)}>
                忽略
              </button>
            </span>
          </div>
        )}

        <div className="composer-status" role="status" aria-live="polite">
          {notice || '建议只作膳食参考；涉及疾病治疗与用药，请咨询专业医生。'}
        </div>
      </footer>
    </main>
  )
}
