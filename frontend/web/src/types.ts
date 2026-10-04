export interface Seasoning {
  name: string
  amount: string
}

/** 主食材（带克数）。
 *  与 seasonings 分开：调料是「一小勺」「一撮」，而营养数值只认克数。
 *  amount_g 为 null 表示正文没给克数——前端不许按常识补一个，那正是这张表最容易骗人的地方。 */
export interface Ingredient {
  name: string
  amount_g?: number | null
}

export interface Recipe {
  name: string
  intro: string
  difficulty: number
  nutrition: number
  ingredients?: Ingredient[]
  /** 口味风格标签（清淡 / 开胃 / 家常…）。
   *  由整理阶段从正文已有的说法里提取；正文没说就是空数组——
   *  前端不许自己按菜名猜一个，那是贴标签不是提取。 */
  flavor_tags?: string[]
  seasonings: Seasoning[]
  steps: string[]
  image_url: string | null
  image_ai_generated: boolean
  image_note?: string
}

export interface HealthLight {
  label: string
  level: 'green' | 'yellow' | 'red'
  reason?: string
}

/** 一项营养：值 + 它到底有没有值。
 *  0 和「没有值」是两件事——0 克膳食纤维是一句结论，「未收录」只是一句实话。 */
export interface NutrientValue {
  value?: number | null
  status: 'known' | 'unavailable'
}

/** 营养账目的一行：这样食材有没有被算进去，算的是国标表里哪一条。 */
export interface NutritionIngredient {
  name: string
  amount_g?: number | null
  /** 映射到的国标表条目名；未收录为 null。页面上可追溯"这个数字查的是哪一条" */
  lookup_name?: string | null
  status: 'covered' | 'missing' | 'no_amount'
}

/** 这顿的营养数值表。由后端按食材用量查国标表算出，**模型不填**。
 *
 *  status 三档是这块最要紧的诚实标注：
 *    complete    全部食材已收录且都有克数
 *    partial     算出了小计，但有食材未收录或缺克数 —— 页面必须说明这是「已覆盖食材的小计」
 *    unavailable 一个都没算成（无食材 / 全未收录 / 全无克数）
 */
export interface NutritionFacts {
  status: 'complete' | 'partial' | 'unavailable'
  /** 键固定为 energy_kcal / protein_g / fat_g / carb_g / fiber_g / sodium_mg */
  nutrients?: Record<string, NutrientValue>
  ingredients?: NutritionIngredient[]
  basis?: string
  covered?: string[]
  missing?: string[]
  no_amount?: string[]
}

export interface SourceRef {
  source: string
  section?: string
  snippet?: string
  category?: string
}

export interface GuardrailItem {
  condition: string
  rule?: string
  status: string
  reason?: string
}

export interface DishMatrixItem {
  dish: string
  member: string
  /** 待确认 = 档案里的过敏原未纳入标准规则，只按原文提醒，需人工确认 */
  verdict: '可吃' | '需调整' | '待确认' | '不可吃'
  reason?: string
}

export interface ChefAnswer {
  opening?: string
  recipes: Recipe[]
  image_url?: string | null
  image_ai_generated?: boolean
  image_requested?: boolean
  image_note?: string
  chef_tip?: string
  sources?: SourceRef[]
  guardrails?: GuardrailItem[]
  health_lights?: HealthLight[]
  /** 这顿的营养数值表（后端算，见 NutritionFacts 的三档状态） */
  nutrition_facts?: NutritionFacts | null
  member_adjustments?: string[]
  dish_matrix?: DishMatrixItem[]
  /** 本轮主菜按哪位成员的健康约束求解（由后端在出卡时注入） */
  primary_member?: string
}

export interface SessionMessage {
  id: number
  user_text: string
  answer: string
  time?: string
  image_name?: string | null
  image_type?: string | null
  image_url?: string | null
  user_image_url?: string | null
  cancelled?: boolean
  image_cancelled?: boolean
  starred?: boolean
  feedback?: 'up' | 'down' | null
}

export interface Session {
  session_id: string
  title?: string
  created_at?: string
  messages?: SessionMessage[]
}

export type DecisionMode = 'home' | 'dining' | 'health'

export type StreamStage = 'thinking' | 'writing' | 'searching' | 'auditing' | 'generating_image' | 'structuring' | 'switching_model'

export interface ChatMessage {
  id: string
  recordId?: number
  feedback?: 'up' | 'down' | null
  starred?: boolean
  role: 'user' | 'assistant'
  text: string
  answer?: ChefAnswer | null
  imageUrl?: string | null
  imageRequested?: boolean
  time?: string
  streaming?: boolean
  imagePending?: boolean
  serverPending?: boolean
  stage?: StreamStage
  elapsed?: number
  error?: boolean
  imageCancelled?: boolean
}

export type WorkspaceView = 'decision' | 'service' | 'weekly' | 'favorites'

export interface FavoriteItem {
  sid: string
  rec_id: number
  session_title: string
  user_text: string
  dish: string
  image_url?: string | null
  answer?: ChefAnswer | null
}

export interface WeeklyReport {
  has_data: boolean
  message?: string
  meals?: number
  top_dishes?: [string, number][]
  lights?: Record<string, number>
  light_trends?: Record<string, string>
  guardrail_triggers?: number
  range?: [string, string]
  recommendations?: string[]
  feedback_summary?: { count: number; tags: Record<string, number> }
}

export interface ServiceRoadmapItem {
  phase: number
  title: string
  status: string
  description: string
}

export interface ServiceVision {
  name: string
  status: string
  current_stage: string
  summary: string
  current_capabilities: string[]
  roadmap: ServiceRoadmapItem[]
  future_dependencies: string[]
  privacy_note: string
}

export interface ServicePreviewRequest {
  recipe_name: string
  inventory_text: string
  image_url?: string | null
  mode?: 'home_chef' | 'voice' | 'text'
  expected_ingredients?: string[]
}

export interface ServicePreviewResult {
  status: string
  mode: string
  recipe_name: string
  recipe_matched: boolean
  required_ingredients: string[]
  detected_from_text: string[]
  missing_ingredients: string[]
  chef_can_bring: string[]
  image_received: boolean
  image_recognition_supported: boolean
  image_recognition_message?: string
  voice_input_received: boolean
  voice_input_supported: boolean
  voice_recognition_available?: boolean
  voice_to_service_integrated?: boolean
  voice_status?: string
  order_supported: boolean
  blocked_reason: string
}


export interface NearbyRestaurant {
  name: string
  cuisine: string
  avg_price: number | null
  distance_km?: number | null
  lng?: number
  lat?: number
  address?: string
  guardrail?: string
}

export interface NearbyResult {
  source: string
  amap_configured: boolean
  restaurants: NearbyRestaurant[]
  warning?: string
}

export interface ResolvedLocation {
  resolved: boolean
  location: string
  city?: string
  district?: string
  label: string
  warning?: string
}

export interface PreferencesData {
  preferences: string
}

export interface MemberBasic {
  height_cm: number | null
  weight_kg: number | null
  age: number | null
  sex: '' | 'male' | 'female' | 'other'
}

export interface MemberProfile {
  basic: MemberBasic
  conditions: string[]
  allergens: string[]
  restricts?: string[]
  goal: string
  diet_style: string
  dislikes: string[]
  taste_notes?: string[]
}

export interface FamilyMember {
  id: string
  name: string
  profile: MemberProfile
}

export interface FamilyData {
  version: number
  active_id: string
  members: FamilyMember[]
}




