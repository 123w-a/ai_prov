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

/** 按能量值确定性换算的运动当量，后端计算，模型不填。 */
export interface ExerciseEquivalent {
  kcal: number
  weight_kg?: number
  /** 固定键 jogging / cycling / walking */
  minutes?: Partial<Record<'jogging' | 'cycling' | 'walking', number>>
  basis?: string
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
  exercise_equiv?: ExerciseEquivalent | null
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
  /** 出处（指南名 + 页码），由后端从确定性规则库注入，不是模型填的。
   *  形如「成人高血压食养指南（2023年版）p6-7」。没有对应规则的条件
   *  （过敏原、成员冲突、档案不可用）为空串——空表示「本产品没给出处」，
   *  前端应显示为无出处，而不是补一个默认值。 */
  source?: string
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
  /**
   * 后端已为「本轮选定目标」校准过 opening（agent/graph.py:3801 / :4523）。
   *
   * 置 true 时**流式正文不得覆盖 opening**。后端 _prefer_streamed_opening
   * （api/routes/chat_route.py:472）就是按这个标志提前返回、拒绝改写；两侧判据
   * 必须一致——后端已拒绝、前端却照旧覆盖，用户看到的是被撤销的那一版，
   * 而标志仍为 true，刷新前无法自愈。
   */
  opening_authoritative?: boolean
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
  /** 那一轮的时间（后端 list_starred 的 turned_at）。**不是**收藏动作时间。 */
  turned_at?: string | null
  /** 收藏动作的时间（后端 list_starred 的 favorited_at，2026-10-11 新增）。
   *  null = 后端没有这个值（旧收藏早于 starred_at 落点），页面显示「待补」。 */
  favorited_at?: string | null
  answer?: ChefAnswer | null
  /** 这条收藏当时的赞/踩（后端 `list_starred` 的 feedback，2026-10-14 新增）。
   *  null / undefined = **从没评过**（不是"没读到"）。
   *  此前接口不带这个值，收藏页只能再拉一遍**整表会话**建映射——收藏越多、
   *  会话越多越亏；现在它跟着收藏条目一起到达。 */
  feedback?: 'up' | 'down' | null
}

export interface WeeklyReport {
  has_data: boolean
  message?: string
  meals?: number
  top_dishes?: [string, number][]
  /**
   * 趋势 sparkline 的真数据（2026-10-10）：营养素 → 含今天最近 7 天的逐日
   * 绿灯占比（0..1；null = 当天该营养素无观测，前端断线不插值）。
   * 与 top_dishes 并行下发，不改既有字段结构（它有四处消费者）。
   */
  light_series?: Record<string, (number | null)[]>
  /** top 菜品里有成品图的那几道：菜名 → 可直接进 <img> 的图片地址。 */
  dish_images?: Record<string, string>
  lights?: Record<string, number>
  light_trends?: Record<string, string>
  guardrail_triggers?: number
  range?: [string, string]
  recommendations?: string[]
  feedback_summary?: { count: number; tags: Record<string, number> }
  /**
   * 逐餐明细（2026-10-14 后端新增，见 api/routes/reports_route.py 的 `_meals_detail`）。
   *
   * 与 top_dishes **并存**、不改既有字段结构（top_dishes 在 agent_tools、旧周报页、
   * ChatArea 与新版周报页四处有消费者）：top_dishes 是「这道菜吃了几次」，
   * 它是「每一次分别是哪一顿」——实测两者能对上（西兰花炒蛋 4 次 ↔ 明细里 4 条）。
   *
   * member 为 null 表示那一顿**没记到人**（老记录没带 member），不许改写成「全家」。
   */
  meals_detail?: Array<{
    ts: string
    dish: string
    member?: string | null
    lights?: Array<{ nutrient: string; level: string }>
    on_target?: boolean
  }>
  /**
   * 每位成员本周一行摘要（2026-10-15 后端新增，见 `_member_week_rows`）。
   *
   * 给周报页的成员卡片用。名单来自 `profile.json`（家里有谁），所以**本周一顿都没
   * 记到的成员也在列表里**，`meals: 0` —— 前端要如实显示"这一周还没记到"，
   * 不能因为数字是 0 就不画这张卡（那会被读成"这个人不在这个家里"）。
   *
   * 这些数字与 `/api/reports/member-weekly` 的一人版**同一套零件**算出
   * （eaters_of / is_meal_on_target / 同一套计数口径），所以卡片上的数
   * 必然等于点进去看到的详情的数。前端**不要**自己再算一遍。
   *
   * `lights` 与家庭版同构（复合键 `"钠:green"` 的计数），前端复用同一个
   * parseLights 拆解 —— 不给它第二个实现。
   */
  members?: Array<{
    name: string
    meals: number
    on_target: number
    lights?: Record<string, number>
    /** 这一周有几顿是跟人一起吃的。多人餐按**共同要求**判定，与独食不可直接比。 */
    shared?: number
    /**
     * 「较上周」（2026-10-15 新增）：上周同期**这位成员自己**的餐数与达标数，
     * 用的是同一把 eater 尺子，不与家庭数混用。
     *
     * 两个都是 0 ⇒ 上周没有他的记录，前端必须显示「上周无记录」，
     * **不许**把 0 读成"上周一餐都没达标"（那是把"不知道"说成"很差"）。
     */
    prev_meals?: number
    prev_on_target?: number
    /**
     * 这位成员本周**最值得说的一句话**，直接显示在卡片上，不要前端再拼。
     *
     * 后端只讲从他自己的记录里能真算出来的事（红灯 > 全达标 > 持续黄 > 没给灯），
     * 因为记账行里只有 ts/dish/lights/guardrails —— 「晚餐蔬菜偏少」这类判断
     * 需要食材与餐段才能下，本仓铁律是不许补造，所以那种话不会出现在这里。
     */
    hint?: string
  }>
  /**
   * 上一周同期整户（2026-10-15 新增，C 设计稿的「较上周 +3 餐」）：
   * 严格等于本周窗口整体前移 7 天，不是"上周一到上周日"那种自然周
   * ——两把尺子量出的差会在跨周界那天突然跳一下。
   *
   * `rate` 在上周零记录时是 **null**，前端显示「上周无记录」；
   * 不能用 0 代替（0 的意思是"上周一餐都没达标"，与"没记"完全不同）。
   */
  prev?: {
    meals: number
    on_target: number
    rate: number | null
    range: [string, string]
  }
  /**
   * 本周达标率（0..1）。与 `rate_series` 里 `is_current` 那一格**同一算法**
   * （都走 is_meal_on_target、同一个分母），这里只是平铺出来给卡片直接用。
   */
  rate?: number | null
  /**
   * 逐周达标率（2026-10-15 新增，C 设计稿「趋势」那张折线）。
   *
   * 与 `light_series` **不是一回事**，别互相顶替：light_series 是近 7 天**逐日**
   * 的灯档位；这里是**逐周**达标率。最旧一周在前、本周在最后。
   *
   * 某周 `rate: null` ⇒ 那周零记录，折线必须在那里**断开**，
   * **不许**连到 0% —— 会画出一条"那周吃得极差"的假谷底。
   */
  rate_series?: Array<{
    start: string
    end: string
    label: string
    meals: number
    on_target: number
    rate: number | null
    is_current: boolean
  }>
}

/**
 * 一位成员的近 7 天周报（2026-10-15 后端新增 `GET /api/reports/member-weekly?name=X`）。
 *
 * 与 `WeeklyReport`（家庭整体）**并列**，不是它的子集：多人同桌的那一顿会同时进
 * 每位参与者的分母，所以这里的数字加起来**不等于**家庭那一份。两个视角回答的是两个
 * 不同的问题（"这位成员吃得怎么样" / "这个家吃得怎么样"），不是一个默认一个下钻。
 *
 * `has_data: false` 时后端只给 name/message/period/days（外加 meals:0、空的
 * light_series 等），所以下面**除了 name/has_data 一律可选**——查无此人的名字也走
 * 这条路（它是"没记到"，不是需要 500 的异常）。
 */
export interface MemberWeeklyReport {
  name: string
  has_data: boolean
  message?: string | null
  period?: { start: string; end: string }
  /** 含今天的 7 个日期（YYYY-MM-DD），与 light_series 的逐个位置一一对应。 */
  days?: string[]
  meals?: number
  progress?: NutritionProgress | null
  light_series?: Record<string, (number | null)[]>
  light_trends?: Record<string, string>
  dishes?: [string, number][]
  dish_images?: Record<string, string>
  recommendations?: string[]
  meals_detail?: Array<{
    ts: string
    dish: string
    member?: string | null
    /** 这一顿一起吃的人（`eaters_of` 的结果）。空/缺 = 那一顿没记到人。 */
    members?: string[]
    lights?: Array<{ nutrient: string; level: string }>
    on_target?: boolean
  }>
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
  /** 孕周（后端 basic.gestational_weeks，2026-10-11 新增）。null = 没登记。
   *  由用户自己录入；后端不做推断，前端更不许由预产期/最后一餐之类倒推。 */
  gestational_weeks?: number | null
  /** 体脂率 %（后端 basic.body_fat_rate，2026-10-11 新增）。null = 没有真值
   *  （用户没填、也没有体脂秤同步）——**不许用 BMI 估算填这个位置**。 */
  body_fat_rate?: number | null
}

/** 成员营养进度（后端 profile.nutrition_progress，2026-10-11 新增）。
 *
 *  口径：本窗口（与周报同一周）内该成员**达标餐次 ÷ 已记录餐次**；
 *  分母为 0 或判据未定时三项皆为 null —— null 的语义是"后端还不知道"，
 *  页面据此显示「待补」，不得改成 0 / 0% / 任何估算百分比。 */
export interface NutritionProgress {
  value: number | null
  numerator: number | null
  denominator: number | null
  period: { start: string; end: string } | null
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
  nutrition_progress?: NutritionProgress | null
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
  /**
   * 家户级共同忌口（**交集**：每个人都忌的那几样），后端 2026-10-14 新增
   * （api/routes/preferences_route.py 的 `_shared_dislikes`）。
   *
   * 可选：`/api/profile/export` 那条链与旧响应里没有这个字段。
   * 为什么这个判定要搬到后端：它原先在前端写了**两遍**且口径相反——
   * household.ts 是交集、firstScreen.ts 是并集。同一个"共同忌口"两个答案，
   * 只能留一个，所以判定收归后端，前端两处都读它。
   */
  shared_dislikes?: string[]
}





