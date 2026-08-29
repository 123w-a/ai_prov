export interface Seasoning {
  name: string
  amount: string
}

export interface Recipe {
  name: string
  intro: string
  difficulty: number
  nutrition: number
  seasonings: Seasoning[]
  steps: string[]
  image_url: string | null
  image_ai_generated: boolean
}

export interface HealthLight {
  label: string
  level: 'green' | 'yellow' | 'red'
  reason?: string
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

export interface ChefAnswer {
  recipes: Recipe[]
  image_url?: string | null
  image_ai_generated?: boolean
  image_note?: string
  chef_tip?: string
  sources?: SourceRef[]
  guardrails?: GuardrailItem[]
  health_lights?: HealthLight[]
}

export interface SessionMessage {
  id: number
  user_text: string
  answer: string
  time?: string
  image_name?: string | null
  image_type?: string | null
  image_url?: string | null
}

export interface Session {
  session_id: string
  title?: string
  created_at?: string
  messages?: SessionMessage[]
}

export type DecisionMode = 'home' | 'dining' | 'fridge' | 'health'

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
  time?: string
  streaming?: boolean
  stage?: StreamStage
  elapsed?: number
  error?: boolean
}

export type WorkspaceView = 'decision' | 'service' | 'weekly' | 'favorites'

export interface FavoriteItem {
  sid: string
  rec_id: number
  session_title: string
  user_text: string
  dish: string
  image_url?: string | null
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
  next_week_shopping?: string[]
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
  address?: string
  guardrail?: string
}

export interface NearbyResult {
  source: string
  amap_configured: boolean
  restaurants: NearbyRestaurant[]
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
  goal: string
  diet_style: string
  dislikes: string[]
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