import type {
  ChefAnswer,
  FamilyData,
  NearbyResult,
  PreferencesData,
  ServicePreviewRequest,
  ServicePreviewResult,
  ServiceVision,
  Session,
} from '../types'

const API_BASE = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').replace(/\/+$/, '')

interface ApiEnvelope<T> {
  code: number
  messages: string
  data: T
}

function cleanError(message: string): string {
  return message.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 240)
}

async function readError(resp: Response): Promise<string> {
  let detail = `HTTP ${resp.status}`
  try {
    const data = (await resp.json()) as { detail?: unknown; messages?: unknown }
    detail = String(data.detail ?? data.messages ?? detail)
  } catch {
    // 非 JSON 响应保留 HTTP 状态码。
  }
  return cleanError(detail)
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, init)
  if (!resp.ok) throw new Error(await readError(resp))
  return resp.json() as Promise<T>
}

export async function fetchSessions(): Promise<Session[]> {
  const data = await jsonRequest<{ sessions: Session[] }>('/api/sessions')
  return data.sessions ?? []
}

export async function createSession(): Promise<Session> {
  const data = await jsonRequest<{ session: Session }>('/api/sessions', { method: 'POST' })
  return data.session
}

export async function deleteSession(sessionId: string): Promise<void> {
  await jsonRequest(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export async function clearSession(sessionId: string): Promise<void> {
  await jsonRequest(`/api/sessions/${encodeURIComponent(sessionId)}/clear`, { method: 'POST' })
}

export async function deleteMessage(sessionId: string, messageId: number): Promise<void> {
  await jsonRequest(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
  )
}

export async function transcribeAudio(audio: File): Promise<string> {
  const body = new FormData()
  body.append('audio', audio)
  const result = await jsonRequest<
    ApiEnvelope<{ text: string; provider: string; available: boolean }>
  >('/api/transcribe', { method: 'POST', body })

  if (result.code !== 200 || !result.data.available) {
    throw new Error(result.messages || '语音识别暂不可用')
  }
  return result.data.text
}

export async function fetchServiceVision(): Promise<ServiceVision> {
  const result = await jsonRequest<ApiEnvelope<ServiceVision>>('/api/service/vision')
  return result.data
}

export async function previewService(
  payload: ServicePreviewRequest,
): Promise<ServicePreviewResult> {
  const result = await jsonRequest<ApiEnvelope<ServicePreviewResult>>('/api/service/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return result.data
}

export interface ChatHandlers {
  onWorking?: () => void
  onToken: (token: string) => void
  onStructuring: () => void
  onAnswer: (answer: ChefAnswer) => void
  onStage?: (stage: string) => void
  onHeartbeat?: (elapsedSeconds: number) => void
  onImage?: (img: { index: number; url: string; ai_generated: boolean }) => void
  onFinish?: () => void
}

export async function sendChat(
  sessionId: string,
  message: string,
  image: File | null,
  mode: string,
  handlers: ChatHandlers,
): Promise<void> {
  const body = new FormData()
  body.append('session_id', sessionId)
  body.append('message', message)
  if (image) body.append('image', image)
  body.append('mode', mode)

  const resp = await fetch(`${API_BASE}/api/chat`, { method: 'POST', body })
  if (!resp.ok) throw new Error(await readError(resp))
  if (!resp.body) throw new Error('后端未返回流式响应')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const line = chunk.replace(/^data:\s?/, '').trim()
      if (!line) continue

      let event: Record<string, unknown>
      try {
        event = JSON.parse(line) as Record<string, unknown>
      } catch {
        continue
      }

      if (event.working || event.status === 'working') handlers.onWorking?.()
      else if (event.heartbeat) handlers.onHeartbeat?.(Number((event.heartbeat as { elapsed?: number })?.elapsed ?? 0))
      else if (event.image) handlers.onImage?.(event.image as { index: number; url: string; ai_generated: boolean })
      else if (event.token != null) handlers.onToken(String(event.token))
      else if (event.structuring) handlers.onStructuring()
      else if (event.answer) handlers.onAnswer(event.answer as ChefAnswer)
      else if (typeof event.stage === 'string') handlers.onStage?.(event.stage)
      else if (event.finish) handlers.onFinish?.()
      else if (event.error) throw new Error(String(event.error))
    }
  }
}


export async function fetchNearby(params?: {
  query?: string
  city?: string
  district?: string
  budget?: number
  location?: string
}): Promise<NearbyResult> {
  const search = new URLSearchParams()
  if (params?.query) search.set("query", params.query)
  if (params?.city) search.set("city", params.city)
  if (params?.district) search.set("district", params.district)
  if (params?.budget != null) search.set("budget", String(params.budget))
  if (params?.location) search.set("location", params.location)
  const result = await jsonRequest<ApiEnvelope<NearbyResult>>(`/api/nearby?${search.toString()}`)
  return result.data
}

export async function fetchPreferences(): Promise<string> {
  const result = await jsonRequest<ApiEnvelope<PreferencesData>>("/api/preferences")
  return result.data.preferences
}

export async function updatePreferences(preferences: string): Promise<string> {
  const result = await jsonRequest<ApiEnvelope<PreferencesData>>("/api/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preferences }),
  })
  return result.data.preferences
}

// ---- P1 家庭多成员画像 ----

export interface MemberInput {
  name: string
  profile: {
    conditions: string[]
    allergens: string[]
    goal: string
    diet_style?: string
    dislikes: string[]
    basic: {
      height_cm: number | null
      weight_kg: number | null
      age: number | null
      sex: '' | 'male' | 'female' | 'other'
    }
  }
}

export async function fetchFamily(): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ exists: boolean; family: FamilyData }>>("/api/profile")
  return result.data.family
}

export async function addMember(member: MemberInput): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>("/api/profile/members", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(member),
  })
  return result.data.family
}

export async function updateMember(memberId: string, member: MemberInput): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>(`/api/profile/members/${memberId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(member),
  })
  return result.data.family
}

export async function deleteMember(memberId: string): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>(`/api/profile/members/${memberId}`, { method: "DELETE" })
  return result.data.family
}

export async function switchActiveMember(memberId: string): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>("/api/profile/active", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_id: memberId }),
  })
  return result.data.family
}

export interface FridgeVisionItem {
  name: string
  quantity: string
}

export async function recognizeFridgePhoto(file: File): Promise<FridgeVisionItem[]> {
  const body = new FormData()
  body.append('image', file)
  const result = await jsonRequest<{ items: FridgeVisionItem[]; draft: boolean; note: string }>(
    '/api/fridge/vision',
    { method: 'POST', body },
  )
  return result.items ?? []
}

export interface MealFeedbackPayload {
  dish: string
  rating: number
  tags: string[]
  comment: string
}

export async function submitMealFeedback(payload: MealFeedbackPayload): Promise<void> {
  await jsonRequest('/api/reports/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}