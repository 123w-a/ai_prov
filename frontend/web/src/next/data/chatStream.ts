import type { ChefAnswer, StreamStage } from '../../types.ts'

/**
 * /api/chat 的真实流式事件。
 *
 * 契约来源（实测）：api/routes/chat_route.py 的 event_generator
 *   1547  {'status':'working'}
 *   1579  {'heartbeat':{'elapsed':N}}       队列空闲时按 poll_timeout 重复
 *   1603  {'token':str}                     已由后端 _looks_like_control_json 过滤过一层
 *   1605  {'stage':str}
 *   1676  {'structuring':True}              正文说完了、正在整理卡片
 *   1677  {'answer':dict}                   整包结构化结果
 *   1681  {'image':{...}}                   图片补完，可能晚于 answer
 *   1686  {'image_failed':{...}}
 *   1691  {'error':str}
 *   1701  {'finish':True,'session_id','record_id'}
 *
 * 两条必须记住的时序事实：
 *   1) structuring 在 answer 之前；answer 到达 ≠ 整轮结束（图片线程最长还要约 25s）。
 *   2) answer_kind === 'candidates' 时后端刻意不推 answer —— 正文本身就是编号候选清单。
 *      见 chat_route.py:1612-1628。此时前端不该等卡片。
 */

export type ChatStreamEvent =
  | { kind: 'working' }
  | { kind: 'heartbeat'; elapsed: number }
  | { kind: 'stage'; stage: StreamStage }
  | { kind: 'token'; text: string }
  | { kind: 'structuring' }
  | { kind: 'answer'; answer: ChefAnswer }
  | { kind: 'image'; recordId?: number; turnId?: string; index: number; url: string; aiGenerated: boolean }
  | { kind: 'image_failed'; recordId?: number; turnId?: string; indexes: number[] }
  | { kind: 'finish'; sessionId?: string; recordId?: number }
  | { kind: 'error'; message: string }

/** 与后端 _looks_like_control_json 同构的兜底：控制用的 JSON 片段不该当正文渲染。 */
function looksLikeControlJson(text: string): boolean {
  const t = text.trim()
  return (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))
}

export interface SendChatParams {
  sessionId: string
  message: string
  image?: File | null
  mode?: string
  wantImage?: boolean
  locationContext?: string | null
}

type Envelope = Record<string, unknown>

export class ChatStreamError extends Error {
  readonly httpStatus: number | undefined
  constructor(message: string, httpStatus?: number) {
    super(message)
    this.name = 'ChatStreamError'
    this.httpStatus = httpStatus
  }
}

/**
 * 消费一次 /api/chat。逐个 yield 事件，让调用方能边收边判断。
 * 抛出 ChatStreamError 表示这一轮失败（HTTP 非 2xx，或流里出现 error 事件）。
 */
export async function* streamChat(
  params: SendChatParams,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const body = new FormData()
  body.append('session_id', params.sessionId)
  body.append('message', params.message)
  if (params.image) body.append('image', params.image)
  body.append('mode', params.mode ?? 'home')
  body.append('want_image', params.wantImage ? '1' : '0')
  if (params.locationContext) body.append('location_context', params.locationContext)

  let resp: Response
  try {
    resp = await fetch('/api/chat', { method: 'POST', body, signal })
  } catch (err) {
    if (signal?.aborted) return
    throw new ChatStreamError(`请求没有发出去：${err instanceof Error ? err.message : String(err)}`)
  }

  // 失败必须在流开始之前就判掉：SSE 一旦 200 打开，后面就没法用状态码区分成败了。
  if (!resp.ok) {
    throw new ChatStreamError(await readErrorText(resp), resp.status)
  }
  if (!resp.body) {
    throw new ChatStreamError('后端没有返回流式响应', resp.status)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const line = frame.replace(/^data:\s?/, '').trim()
      if (!line) continue

      let envelope: Envelope
      try {
        envelope = JSON.parse(line) as Envelope
      } catch {
        // 半截帧不该让整轮崩掉：丢掉继续读。
        continue
      }

      const event = toEvent(envelope)
      if (event === null) continue
      if (event.kind === 'error') throw new ChatStreamError(event.message)
      yield event
    }
  }
}

function toEvent(envelope: Envelope): ChatStreamEvent | null {
  if (envelope.working || envelope.status === 'working') return { kind: 'working' }

  if (envelope.heartbeat != null) {
    const hb = envelope.heartbeat as { elapsed?: number }
    return { kind: 'heartbeat', elapsed: Number(hb?.elapsed ?? 0) }
  }

  if (envelope.image != null) {
    const img = envelope.image as {
      record_id?: number
      turn_id?: string
      index: number
      url: string
      ai_generated: boolean
    }
    return {
      kind: 'image',
      recordId: img.record_id,
      turnId: img.turn_id,
      index: img.index,
      url: img.url,
      aiGenerated: Boolean(img.ai_generated),
    }
  }

  if (envelope.image_failed != null) {
    const f = envelope.image_failed as { record_id?: number; turn_id?: string; indexes: number[] }
    return { kind: 'image_failed', recordId: f.record_id, turnId: f.turn_id, indexes: f.indexes ?? [] }
  }

  if (typeof envelope.token === 'string') {
    // 后端已过滤一层，这里再兜一层：宁可少渲染一行，也不把控制 JSON 画到正文里。
    if (looksLikeControlJson(envelope.token)) return null
    return { kind: 'token', text: envelope.token }
  }

  if (envelope.structuring) return { kind: 'structuring' }

  if (envelope.answer != null) {
    return { kind: 'answer', answer: envelope.answer as ChefAnswer }
  }

  if (typeof envelope.stage === 'string') {
    return { kind: 'stage', stage: envelope.stage as StreamStage }
  }

  if (envelope.finish) {
    return {
      kind: 'finish',
      sessionId: typeof envelope.session_id === 'string' ? envelope.session_id : undefined,
      recordId: typeof envelope.record_id === 'number' ? envelope.record_id : undefined,
    }
  }

  if (envelope.error) return { kind: 'error', message: String(envelope.error) }

  return null
}

async function readErrorText(resp: Response): Promise<string> {
  const fallback = `HTTP ${resp.status}`
  try {
    const text = (await resp.text()).trim()
    if (!text) return fallback
    try {
      const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown; error?: unknown }
      const detail = parsed.detail ?? parsed.message ?? parsed.error
      if (typeof detail === 'string' && detail) return detail
    } catch {
      // 不是 JSON，原样返回
    }
    return text.slice(0, 300)
  } catch {
    return fallback
  }
}
