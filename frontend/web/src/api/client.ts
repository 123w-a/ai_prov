import type {
  ChefAnswer,
  FamilyData,
  FamilyMember,
  NearbyResult,
  ResolvedLocation,
  PreferencesData,
  ServicePreviewRequest,
  ServicePreviewResult,
  ServiceVision,
  FavoriteItem,
  Session,
  MemberWeeklyReport,
  WeeklyReport,
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

export interface ApiHealth {
  code: number
  messages: string
  data: null
}

/** 后端根路由自检，与进程探针 `/api/health/*` 分开。 */
export async function fetchApiHealth(): Promise<ApiHealth> {
  return jsonRequest<ApiHealth>('/api/')
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

export async function renameSession(sessionId: string, title: string): Promise<void> {
  await jsonRequest(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export async function deleteMessage(sessionId: string, messageId: number): Promise<void> {
  await jsonRequest(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
  )
}

export interface SessionMessageInput {
  userText: string
  answer: string
  time?: string
  imageName?: string
  imageType?: string
  image?: File | null
}

/** 后端兼容入口：手动补一条问答记录。常规聊天仍由 `/api/chat` 自动落库。 */
export async function appendSessionMessage(
  sessionId: string,
  input: SessionMessageInput,
): Promise<void> {
  const body = new FormData()
  body.append('user_text', input.userText)
  body.append('answer', input.answer)
  body.append('time', input.time ?? new Date().toISOString())
  if (input.imageName) body.append('image_name', input.imageName)
  if (input.imageType) body.append('image_type', input.imageType)
  if (input.image) body.append('image', input.image)
  await jsonRequest<{ ok: boolean; msg: string }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: 'POST', body },
  )
}

/**
 * 语音转写。端点实测为 `/api/transcribe`（POST，字段名 `audio`）——
 * 后端不存在 `/api/speech`（实测 404，见交付证据），方案里那个路径是笔误。
 *
 * `signal` 是 2026-10-12 新增的**可选**参数：新前端书写面的麦克风在组件卸载时
 * 要真的断掉在途转写（否则用户切房/卸载后仍留一个后台请求）。老前端调用点
 * （App.tsx 的 onTranscribe）不传，行为不变——加参数不改语义，也不改返回。
 */
export async function transcribeAudio(audio: File, signal?: AbortSignal): Promise<string> {
  const body = new FormData()
  body.append('audio', audio)
  const result = await jsonRequest<
    ApiEnvelope<{ text: string; provider: string; available: boolean }>
  >('/api/transcribe', { method: 'POST', body, signal })

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
  onImage?: (img: { record_id?: number; turn_id?: string; index: number; url: string; ai_generated: boolean }) => void
  onImageFailed?: (payload: { record_id?: number; turn_id?: string; indexes: number[] }) => void
  onFinish?: (payload: { session_id?: string; record_id?: number }) => void
  /**
   * 护栏打回重生成（main.py:470 发事件 → api/routes/chat_route.py:1901 转发）。
   *
   * 上一版正文此刻已经整段流式外发过了，后端在同一条链路上清空了自己的累积缓冲
   * （_accumulate_streamed_text 的 reset 分支）。前端必须同步丢弃已渲染的正文，
   * 否则第二版会首尾拼在第一版后面——用户在同一轮里看到同一道菜讲两遍，
   * 落库的 opening 也会是两遍。
   *
   * 这是控制事件、不是可展示内容：清空即可，不要往气泡里写任何提示语。
   */
  onReset?: () => void
}

export interface ChatImageTarget {
  recordId?: number
  recipeIndex?: number
  dishName?: string
}

export async function cancelDecision(
  sessionId: string,
  turnId?: string,
  keepText = false,
): Promise<void> {
  const body = new FormData()
  body.append('session_id', sessionId)
  if (turnId) body.append('turn_id', turnId)
  body.append('keep_text', keepText ? '1' : '0')
  await jsonRequest('/api/chat/cancel-image', { method: 'POST', body })
}

export async function sendChat(
  sessionId: string,
  message: string,
  image: File | null,
  mode: string,
  wantImage: boolean,
  locationContext: string | null,
  imageTarget: ChatImageTarget | null,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  turnId?: string,
): Promise<void> {
  const body = new FormData()
  body.append('session_id', sessionId)
  body.append('message', message)
  if (turnId) body.append('turn_id', turnId)
  if (image) body.append('image', image)
  body.append('mode', mode)
  body.append('want_image', wantImage ? '1' : '0')
  if (locationContext) body.append('location_context', locationContext)
  if (imageTarget?.recordId != null) body.append('target_record_id', String(imageTarget.recordId))
  if (imageTarget?.recipeIndex != null) body.append('target_recipe_index', String(imageTarget.recipeIndex))
  if (imageTarget?.dishName) body.append('target_dish_name', imageTarget.dishName)

  const resp = await fetch(`${API_BASE}/api/chat`, { method: 'POST', body, signal })
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

      // reset 必须排在最前面判：它是控制事件，与其它键不共存；一旦被后面的
      // else-if 链漏过去，就永远不会清空。（历史缺口：这里没有 reset 分支，
      // 护栏打回时前端照旧把两版正文接在一起。）
      if (event.reset) handlers.onReset?.()
      else if (event.working || event.status === 'working') handlers.onWorking?.()
      else if (event.heartbeat) handlers.onHeartbeat?.(Number((event.heartbeat as { elapsed?: number })?.elapsed ?? 0))
      else if (event.image) handlers.onImage?.(event.image as { record_id?: number; turn_id?: string; index: number; url: string; ai_generated: boolean })
      else if (event.image_failed) handlers.onImageFailed?.(event.image_failed as { record_id?: number; turn_id?: string; indexes: number[] })
      else if (event.token != null && typeof event.token === 'string') {
        const token = event.token.trim()
        if (
          (token.startsWith('{') && token.endsWith('}')) ||
          (token.startsWith('[') && token.endsWith(']'))
        ) {
          continue
        }
        handlers.onToken(event.token)
      }
      else if (event.structuring) handlers.onStructuring()
      else if (event.answer) handlers.onAnswer(event.answer as ChefAnswer)
      else if (typeof event.stage === 'string') handlers.onStage?.(event.stage)
      else if (event.finish) handlers.onFinish?.(event as { session_id?: string; record_id?: number })
      else if (event.error) throw new Error(String(event.error))
    }
  }
}


export interface NearbyQuery {
  query?: string
  city?: string
  district?: string
  budget?: number
  location?: string
  radius?: number
  page?: number
}

function nearbySearchParams(params?: NearbyQuery): URLSearchParams {
  const search = new URLSearchParams()
  if (params?.query) search.set("query", params.query)
  if (params?.city) search.set("city", params.city)
  if (params?.district) search.set("district", params.district)
  if (params?.budget != null) search.set("budget", String(params.budget))
  if (params?.location) search.set("location", params.location)
  if (params?.radius != null) search.set("radius", String(params.radius))
  if (params?.page != null) search.set("page", String(params.page))
  return search
}

export async function fetchNearby(params?: NearbyQuery): Promise<NearbyResult> {
  const search = nearbySearchParams(params)
  const result = await jsonRequest<ApiEnvelope<NearbyResult>>(`/api/nearby?${search.toString()}`)
  return result.data
}

export interface MapCoordinate {
  lng: number
  lat: number
}

export interface NearbyMapMarker {
  name: string
  cuisine: string
  avg_price: number
  distance_km?: number | null
  lng: number
  lat: number
  address: string
  guardrail: string
}

export interface NearbyMapResult {
  source: string
  amap_configured: boolean
  coordinate_system: 'gcj02'
  center: MapCoordinate | null
  origin: MapCoordinate | null
  markers: NearbyMapMarker[]
  warning: string
  allergen_filter?: {
    applied: boolean
    removed: number
    message: string
  }
}

/**
 * 地图视图模型。与 `/api/nearby` 共用筛选口径，只增加坐标、中心点与 marker；
 * `source` 不是 `amap` 时 marker 必须为空，调用方不得补造坐标。
 */
export async function fetchNearbyMap(params?: NearbyQuery): Promise<NearbyMapResult> {
  const search = nearbySearchParams(params)
  const result = await jsonRequest<ApiEnvelope<NearbyMapResult>>(
    `/api/nearby/map?${search.toString()}`,
  )
  return result.data
}

export async function resolveLocation(location: string): Promise<ResolvedLocation> {
  const search = new URLSearchParams()
  search.set('location', location)
  const result = await jsonRequest<ApiEnvelope<ResolvedLocation>>(`/api/location/resolve?${search.toString()}`)
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
    restricts?: string[]
    goal: string
    diet_style?: string
    dislikes: string[]
    taste_notes?: string[]
    basic: {
      height_cm: number | null
      weight_kg: number | null
      age: number | null
      sex: '' | 'male' | 'female' | 'other'
      /** 孕周（2026-10-11 新增，可空）。用户录入。 */
      gestational_weeks?: number | null
      /** 体脂率（2026-10-11 新增，可空）。用户录入/体脂秤同步，后端不推算。 */
      body_fat_rate?: number | null
    }
  }
}

export interface MemoryCandidate {
  id: string
  session_id?: string
  member: string
  member_id: string
  dimension: string
  value: string
  severity: 'hard' | 'soft'
  scope: 'always' | 'once'
  source_text: string
  status: 'pending' | 'once' | 'confirmed' | 'dismissed'
}

/**
 * 拉取待确认的偏好候选。
 *
 * sessionId 改为**可选**（2026-09-28）：首屏 IA 骨架要展示的是「全局有哪些待确认事项」，
 * 不属于任何一次会话——带上 session_id 会把它过滤掉，那条 2026-09-22 的候选在全新
 * 会话里就看不见了。实测该端点**不带参数即返回全部 pending**（200 / 400B）。
 * 现有两处调用（App.tsx L726、L1002）都传了 sessionId，行为不变。
 */
export async function fetchPendingMemoryCandidates(sessionId?: string): Promise<MemoryCandidate[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const result = await jsonRequest<ApiEnvelope<{ candidates: MemoryCandidate[] }>>(
    `/api/preferences/candidates/pending${query}`,
  )
  return result.data.candidates ?? []
}

export async function confirmMemoryCandidate(candidateId: string): Promise<MemoryCandidate> {
  const result = await jsonRequest<ApiEnvelope<{ candidate: MemoryCandidate }>>(
    `/api/preferences/candidates/${encodeURIComponent(candidateId)}/confirm`,
    { method: 'POST' },
  )
  return result.data.candidate
}

export async function rememberMemoryCandidateOnce(candidateId: string): Promise<MemoryCandidate> {
  const result = await jsonRequest<ApiEnvelope<{ candidate: MemoryCandidate }>>(
    `/api/preferences/candidates/${encodeURIComponent(candidateId)}/once`,
    { method: 'POST' },
  )
  return result.data.candidate
}

export async function dismissMemoryCandidate(candidateId: string): Promise<void> {
  await jsonRequest(
    `/api/preferences/candidates/${encodeURIComponent(candidateId)}/dismiss`,
    { method: 'POST' },
  )
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

/** 旧前端使用的单数路径别名；新代码优先用 `deleteMember`。 */
export async function deleteMemberLegacy(memberId: string): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>(
    `/api/profile/member/${encodeURIComponent(memberId)}`,
    { method: "DELETE" },
  )
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

export type HealthProfileInput = MemberInput['profile']

/** 兼容旧前端的整档写入：只更新当前激活成员，不新增成员。 */
export async function replaceActiveProfile(profile: HealthProfileInput): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>("/api/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  })
  return result.data.family
}

export interface FamilyExport {
  app: string
  version: number
  exported_at: string
  active_id: string
  members: FamilyMember[]
}

export async function exportFamily(): Promise<FamilyExport> {
  const result = await jsonRequest<ApiEnvelope<{ export: FamilyExport }>>("/api/profile/export")
  return result.data.export
}

export async function importFamily(members: MemberInput[]): Promise<FamilyData> {
  const result = await jsonRequest<ApiEnvelope<{ family: FamilyData }>>("/api/profile/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ members }),
  })
  return result.data.family
}

// ---- 回答满意度反馈（卡片 👍/👎 + 周统计）----

export type FeedbackRating = 'up' | 'down'

export async function sendMessageFeedback(
  sessionId: string,
  recordId: number,
  rating: FeedbackRating,
): Promise<'up' | 'down' | null> {
  const result = await jsonRequest<ApiEnvelope<{ feedback: 'up' | 'down' | null }>>(
    `/api/sessions/${sessionId}/messages/${recordId}/feedback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating }),
    },
  )
  return result.data.feedback
}

export interface FeedbackWeekly {
  up: number
  down: number
  total: number
  down_dishes: string[]
  down_items?: Array<{ dish: string; count: number }>
}

export async function fetchFeedbackWeekly(): Promise<FeedbackWeekly> {
  const result = await jsonRequest<ApiEnvelope<FeedbackWeekly>>("/api/feedback/weekly")
  return result.data
}

export async function forgetDish(dish: string): Promise<{ removed: number; dish: string }> {
  const result = await jsonRequest<ApiEnvelope<{ removed: number; dish: string }>>(
    '/api/feedback/forget-dish',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dish }),
    },
  )
  return result.data
}

export async function fetchWeeklyReport(): Promise<WeeklyReport> {
  return jsonRequest<WeeklyReport>("/api/reports/weekly")
}

/**
 * 某位成员的近 7 天周报（2026-10-15 接线，配周报页的成员卡片）。
 *
 * 用**名字**而不是 id：整条归属链（`meals.json` 的 `member`/`members`、后端的
 * `by_member` 键、回填脚本的匹配）都以名字为准，另起一套 id 会多出一个可能与名字
 * 不同步的身份系统。名字走 encodeURIComponent——中文名与可能带上的空格/斜杠都照传。
 *
 * 与 `fetchWeeklyReport` 一样是裸返回（`/api/reports/*` 这族不走 ApiEnvelope）。
 * 查不到这位成员的餐时后端返回 `has_data: false`，**不是**错误码——调用方按诚实空态处理。
 */
export async function fetchMemberWeekly(name: string): Promise<MemberWeeklyReport> {
  return jsonRequest<MemberWeeklyReport>(
    `/api/reports/member-weekly?name=${encodeURIComponent(name)}`,
  )
}

/** 单条饮食建议。`kind` 决定前端怎么标注来源语气：
 *  intake=某项超标要改 / positive=这项没问题 / progress=在变好 / variety=菜式轮换 / sample=样本不足。 */
export interface IntakeTip {
  text: string
  kind: 'intake' | 'positive' | 'progress' | 'variety' | 'sample'
  metric: string | null
  detail: string | null
}

export interface IntakeTips {
  basis: 'ok' | 'insufficient'
  window: { meals: number; latest: string | null; earliest: string | null }
  meals: number
  metrics: Record<string, { off: number; total: number; ratio: number }>
  tips: IntakeTip[]
}

/**
 * 「今日饮食建议」：按最近几餐的成分摄入水平算出来的要点（2026-10-19 接线）。
 *
 * 后端只取最近 6 餐、按**家庭整体**口径算，不分成员——原因是记账里带成员归属的
 * 比例还很低，按人筛会只剩个位数样本、永远出不了方向性结论。
 * 与 `/api/reports/*` 这族一样是裸返回；样本不足时 `basis: "insufficient"`，
 * `tips` 里会有一条诚实说明，**不是**错误码。
 */
export async function fetchIntakeTips(): Promise<IntakeTips> {
  return jsonRequest<IntakeTips>('/api/reports/intake-tips')
}

export interface WeeklySummaryResponse {
  ai_summary: string | null
  reason?: string
  cached?: boolean
}export async function fetchWeeklySummary(refresh = false): Promise<WeeklySummaryResponse> {
  return jsonRequest<WeeklySummaryResponse>(
    `/api/reports/weekly-summary${refresh ? "?refresh=1" : ""}`,
  )
}

export interface MealAttributionDiagnostics {
  total: number
  attributed: number
  unattributed: number
  by_member: Record<string, number>
  period: { start: string; end: string }
  attributed_this_week: number
  unattributed_this_week: number
}

/** 只读诊断口：记账中的成员归属现状。 */
export async function fetchMealAttribution(): Promise<MealAttributionDiagnostics> {
  return jsonRequest<MealAttributionDiagnostics>("/api/reports/meal-attribution")
}

export async function starMessage(
  sessionId: string,
  recordId: number,
  starred: boolean,
): Promise<{ starred: boolean }> {
  const result = await jsonRequest<ApiEnvelope<{ starred: boolean }>>(
    `/api/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(recordId)}/star`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ starred }),
    },
  )
  return result.data
}

export async function fetchFavorites(): Promise<FavoriteItem[]> {
  const result = await jsonRequest<ApiEnvelope<{ favorites: FavoriteItem[] }>>('/api/favorites')
  return result.data.favorites
}

export async function addDislike(item: string): Promise<void> {
  await jsonRequest('/api/profile/dislikes/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ item }),
  })
}

export interface TasteSuggestion {
  taste: string
  count: number
  note_label: string
}

export async function fetchTasteSuggestion(): Promise<TasteSuggestion | null> {
  const result = await jsonRequest<ApiEnvelope<{ suggestion: TasteSuggestion | null }>>(
    '/api/profile/taste-suggestion',
  )
  return result.data.suggestion
}

export async function addTasteNote(text: string): Promise<void> {
  await jsonRequest('/api/profile/taste-note', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
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

/**
 * 冰箱现有食材。注意这个端点**不包 ApiEnvelope**，直接返回 {items:[...]}。
 * 等待页只用它做一行"已知输入"的证据，不参与任何加工。
 */
export async function fetchFridge(): Promise<string[]> {
  const result = await jsonRequest<{ items?: string[] }>('/api/fridge')
  return result.items ?? []
}

/**
 * 覆盖冰箱清单（`POST /api/fridge/set`，2026-10-14 接线）。
 *
 * **传进去的每一样都代表"还在冰箱里"**——后端把这次没出现的旧项记成「吃掉/用掉」
 * （`api/routes/fridge_route.py` 的 `set_fridge`）。所以"删掉一样"就是传"剩下那几样"，
 * 不需要单独的删除端点；后端也就顺手有了消耗流水，周报的补货建议靠它。
 *
 * 用换行拼接：后端切分符里包含 `\n`（`service_route.py` 的 `_split_inventory_text`），
 * 而食材名里不可能有换行——比用顿号拼稳（名字里真带顿号就会被凭空裂成两样）。
 */
export async function setFridge(items: string[]): Promise<string[]> {
  return postFridge('/api/fridge/set', items.join('\n'))
}

/**
 * 往冰箱里补货（`POST /api/fridge/add`，追加去重，不覆盖）。
 *
 * `text` 是**用户原样输入**，前端不切分：后端会清掉数量单位、剥掉"家里还有"这类前缀，
 * 再按顿号/逗号/换行/斜杠切开。切分规则只留后端一份——两处各写一遍，迟早出现
 * "前端切出 3 样、后端切出 2 样"的分叉，而这种分叉只会在用户输入带数字时才露出来。
 */
export async function addFridge(text: string): Promise<string[]> {
  return postFridge('/api/fridge/add', text)
}

/** 两个冰箱写端点同形：`items` 是 **form 字段**（后端签名是 `items: str = Form("")`），
 *  而不是 JSON body——按 JSON 发会拿到 422，这是接这两个端点最容易踩的一步。
 *  返回的是后端写盘后的**权威清单**，调用方拿它刷新即可，不必再 GET 一次。 */
async function postFridge(path: string, items: string): Promise<string[]> {
  const body = new URLSearchParams()
  body.append('items', items)
  const result = await jsonRequest<{ items?: string[] }>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
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

// ---- 运维探针 ----

export interface HealthReadinessPayload {
  status: string
  checks: Record<string, boolean>
  rag: string
  time: string
}

export interface HealthReadinessResult extends HealthReadinessPayload {
  http_status: 200 | 503
  ready: boolean
}

export async function fetchLiveness(): Promise<{ status: string }> {
  return jsonRequest<{ status: string }>('/api/health/live')
}

/**
 * 就绪探针的 503 是有效业务响应，不能当作普通 HTTP 错误丢掉 checks。
 * 只有 200/503 以外的状态才抛错。
 */
export async function fetchReadiness(): Promise<HealthReadinessResult> {
  const resp = await fetch(`${API_BASE}/api/health/ready`)
  if (resp.status !== 200 && resp.status !== 503) {
    throw new Error(await readError(resp))
  }
  const payload = (await resp.json()) as HealthReadinessPayload
  return {
    ...payload,
    http_status: resp.status,
    ready: resp.status === 200 && payload.status === 'ok',
  }
}

export interface HealthStatus {
  status: string
  startup: {
    started_at: number
    knowledge_base: string
    knowledge_base_error: string
    knowledge_base_ready_at?: number
    uptime_seconds: number
  }
}

export async function fetchHealthStatus(): Promise<HealthStatus> {
  return jsonRequest<HealthStatus>('/api/health/status')
}

// ---- 知识库检索（评测接口，也可供管理员/调试页复用）----

export interface KbSearchHit {
  text: string
  distance: number
}

export interface KbSearchResult {
  query: string
  count: number
  hits: KbSearchHit[]
}

export async function searchKnowledgeBase(query: string, k = 5): Promise<KbSearchResult> {
  return jsonRequest<KbSearchResult>('/api/kb/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, k }),
  })
}


