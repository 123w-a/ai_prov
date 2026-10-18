/**
 * 语音输入的状态机与副作用（MediaRecorder → POST /api/transcribe → 回填草稿）。
 *
 * 语义照抄老前端 src/components/ChatArea.tsx 的 startVoiceRecording /
 * finishVoiceRecording（约 :443-509）：同一套 MediaRecorder 用法、同一套
 * mime 兜底、同一句"没有录到声音"。**不另创一套**——两处录音行为一旦分叉，
 * 用户会看到"这页的麦克风跟那页不一样"，而那种差异极难复现。
 *
 * 三处它做得更严，都是这次验收点名的：
 *   ① 三态显式建模（idle / recording / transcribing），不是靠 notice 文本推；
 *   ② 识别结果**回填光标处、不自动发送**——插入算法在 voiceText.ts（有测试）；
 *   ③ 卸载时真的断掉：abort 在途转写请求、摘掉 recorder 监听、stop 所有 tracks。
 *      只 stop tracks 不 abort 请求，会留下一个还在跑的后台请求（老前端即如此）。
 *
 * 隐私边界写在界面上（AskVoice 里那一句），这里只负责：音频**只**发到
 * `${API_BASE}/api/transcribe`，不落 localStorage、不进任何日志、不做本地留存。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { transcribeAudio } from '../../api/client.ts'
import {
  EMPTY_AUDIO_NOTE,
  EMPTY_TRANSCRIPT_NOTE,
  TRANSCRIBED_NOTE,
  voiceErrorMessage,
} from './voiceText.ts'

export type VoiceState = 'idle' | 'recording' | 'transcribing'
export type VoiceSupport = 'ready' | 'insecure' | 'unsupported'

/**
 * 浏览器是否真的有录音能力。能力不足时按钮仍保留，点击后说明原因，
 * 避免同一个操作行有时有麦克风、有时只剩相机，用户不知道发生了什么。
 */
function detectSupport(): VoiceSupport {
  if (typeof window === 'undefined') return 'unsupported'
  if (window.isSecureContext === false) return 'insecure'
  if (typeof MediaRecorder === 'undefined') return 'unsupported'
  if (typeof navigator === 'undefined') return 'unsupported'
  const devices = navigator.mediaDevices
  return devices && typeof devices.getUserMedia === 'function' ? 'ready' : 'unsupported'
}

function supportNotice(support: VoiceSupport): string | null {
  if (support === 'ready') return null
  if (support === 'insecure') {
    return '麦克风只能在 localhost 或 HTTPS 页面使用，请用 http://127.0.0.1:5178/ 打开。'
  }
  return '当前浏览器没有提供录音能力，请使用最新版 Chrome、Edge 或 Safari。'
}

/** 优先 opus/webm；Safari 只给 mp4，此时返回空串交给 MediaRecorder 自己选。 */
function preferredMime(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) return 'audio/webm;codecs=opus'
  if (MediaRecorder.isTypeSupported('audio/webm')) return 'audio/webm'
  return ''
}

function stopTracks(stream: MediaStream | null) {
  if (!stream) return
  stream.getTracks().forEach((track) => track.stop())
}

export function useVoiceInput(onTranscript: (text: string) => void) {
  const [support] = useState<VoiceSupport>(detectSupport)
  const supported = support === 'ready'
  const [state, setState] = useState<VoiceState>('idle')
  const [notice, setNotice] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const aliveRef = useRef(true)
  /** 回调放进 ref：识别回来时用的是**当时**那个回填函数，不因父组件重渲染而失联。 */
  const cbRef = useRef(onTranscript)
  cbRef.current = onTranscript

  /** 录音结束 → 转写 → 回填。任何一条失败都如实写出来，绝不静默。 */
  const finish = useCallback(async (mime: string) => {
    stopTracks(streamRef.current)
    streamRef.current = null
    recorderRef.current = null
    const chunks = chunksRef.current
    chunksRef.current = []

    const blob = new Blob(chunks, { type: mime || 'audio/webm' })
    if (blob.size === 0) {
      setState('idle')
      setNotice(EMPTY_AUDIO_NOTE)
      return
    }

    const isM4a = /mp4|m4a/i.test(mime)
    const audio = new File([blob], `voice-${Date.now()}.${isM4a ? 'm4a' : 'webm'}`, {
      type: isM4a ? 'audio/m4a' : mime || 'audio/webm',
    })

    setState('transcribing')
    setNotice(null)
    const ac = new AbortController()
    abortRef.current = ac
    try {
      const text = await transcribeAudio(audio, ac.signal)
      if (!aliveRef.current) return
      const clean = text.trim()
      if (clean.length === 0) {
        // 空结果**不动草稿**：识别"成功"但没内容，不该把已写的字擦掉。
        setNotice(EMPTY_TRANSCRIPT_NOTE)
        return
      }
      cbRef.current(clean)
      setNotice(TRANSCRIBED_NOTE)
    } catch (err) {
      if (!aliveRef.current) return
      if (err instanceof DOMException && err.name === 'AbortError') return
      setNotice(voiceErrorMessage(err, 'transcribe'))
    } finally {
      abortRef.current = null
      if (aliveRef.current) setState('idle')
    }
  }, [])

  const start = useCallback(async () => {
    if (state !== 'idle') return
    setNotice(null)
    if (!supported) {
      setNotice(supportNotice(support))
      return
    }
    let stream: MediaStream | null = null
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!aliveRef.current) {
        stopTracks(stream)
        return
      }
      const mime = preferredMime()
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream)
      streamRef.current = stream
      recorderRef.current = recorder
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        void finish(recorder.mimeType || mime || 'audio/webm')
      }
      recorder.start()
      setState('recording')
    } catch (err) {
      stopTracks(stream)
      if (!aliveRef.current) return
      setState('idle')
      // 权限被拒**不隐藏按钮**：按钮仍是"再试一次"的入口，原因写在它旁边。
      setNotice(voiceErrorMessage(err, 'mic'))
    }
  }, [supported, support, state, finish])

  const stop = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop() // onstop 里接着转写
      return
    }
    stopTracks(streamRef.current)
    streamRef.current = null
    setState('idle')
  }, [])

  const toggle = useCallback(() => {
    if (state === 'recording') stop()
    else if (state === 'idle') void start()
  }, [state, start, stop])

  /** 说明不会永久占位：浮层 12 秒后自己退场，原因已经写过了。 */
  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), 12000)
    return () => window.clearTimeout(t)
  }, [notice])

  /**
   * 卸载 = 拆干净。顺序有意义：
   *   ① 先 abort 转写请求（否则留下一个还在跑的后台请求）；
   *   ② 再摘 recorder 的 onstop（否则 stop() 会触发一次"转写"，往已卸载的组件里写状态）；
   *   ③ 最后 stop tracks —— 不做这一步，浏览器一直显示"正在录音"。
   */
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      abortRef.current?.abort()
      abortRef.current = null
      const recorder = recorderRef.current
      recorderRef.current = null
      if (recorder) {
        recorder.onstop = null
        recorder.ondataavailable = null
        if (recorder.state !== 'inactive') {
          try {
            recorder.stop()
          } catch {
            /* 已经停了 */
          }
        }
      }
      stopTracks(streamRef.current)
      streamRef.current = null
      chunksRef.current = []
    }
  }, [])

  return { supported, supportNotice: supportNotice(support), state, notice, toggle }
}
