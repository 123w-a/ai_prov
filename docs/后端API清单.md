# 小膳管家 · 后端 API 契约清单

> **生成方式**：以运行时 `api.main_app:app.openapi()` 为准，并与 `frontend/web/src/api/client.ts`、`types.ts` 交叉核对。
> **代码基线**：当前工作区 `codex/frontend-backend-fusion`，HEAD `6e9d035`，包含 2026-10-18 尚未提交的后端改动。
> **端点总数**：52 个 method/path 操作。
> **前端接口层**：52/52 均已有调用封装；具体放在哪个页面尚未决定。

---

## 1. 全局约定

| 项目 | 值 |
|---|---|
| 路由前缀 | 全部统一 `/api`（`main_app.py` 中 `include_router(..., prefix="/api")`） |
| 后端端口 | `python run.py` 默认 `http://127.0.0.1:8010` |
| 前端 dev server | `http://localhost:5178`，`/api` 由 Vite 代理到 `127.0.0.1:8010` |
| CORS 白名单 | 后端显式放行 `localhost:5173`、`127.0.0.1:5173`、`localhost:3000`、`127.0.0.1:3000`；5178 通过同源代理访问 |
| 前端 base 配置 | 环境变量 `VITE_API_BASE`；留空则走同源相对路径 |
| 全局异常 | 500 + `{code:500, messages:"服务内部错误，请稍后重试", data:null, request_id}`，响应头附 `X-Request-ID` |
| 请求体上限 | 默认 16MB，环境变量 `CHEF_MAX_REQUEST_MB` 可调；超限返回 413，结构与全局异常一致 |
| 参数校验失败 | FastAPI 原生格式 `{detail:[...]}` |
| 编码 | 统一 UTF-8 |

---

## 2. ⚠️ 三种响应形态（重建前端最大的坑）

后端**没有统一的响应约定**，同一个前端必须同时处理三种：

### A 型 · 统一信封（约占 2/3）

```json
{ "code": 200, "messages": "人类可读提示", "data": { /* 业务数据 */ } }
```

判定方式：`client.ts` 中以 `ApiEnvelope<T>` 包裹的调用。
注意 `data` 可能为 `null`（如 `GET /api/`），部分端点还会省略 `messages`。

### B 型 · 裸 JSON

直接返回业务对象，**没有** `code` / `messages` 外层。典型：

```json
{ "sessions": [...] }      // GET /api/sessions
{ "ok": true, "msg": "..." }  // DELETE /api/sessions/{sid}
{ "has_data": true, ... }  // GET /api/reports/weekly
```

### C 型 · SSE 事件流

仅 `POST /api/chat` 一个端点，`media_type="text/event-stream"`。详见第 3 节。

---

## 3. SSE 协议 · `POST /api/chat`

**请求**：`multipart/form-data`（不是 JSON）

| 字段 | 必填 | 说明 |
|---|---|---|
| `session_id` | 是 | 会话 ID |
| `message` | 是 | 用户输入文本 |
| `turn_id` | 否 | 本轮 ID，用于后续取消配图 |
| `image` | 否 | 用户上传的图片文件 |
| `mode` | 否 | `home`（默认）/ `dining` / `health` |
| `want_image` | 否 | `"1"` / `"0"`（字符串，不是布尔） |
| `location_context` | 否 | 位置上下文，用于附近餐厅 |
| `target_record_id` | 否 | 为历史某条记录补图 |
| `target_recipe_index` | 否 | 指定给第几道菜配图 |
| `target_dish_name` | 否 | 指定菜名配图 |

**响应**：`text/event-stream`，事件之间以 `\n\n` 分隔，每个事件形如 `data: {json}`。

**事件判定有严格顺序**（前端必须按此顺序做 `else-if`，否则会误判）：

| # | 事件 JSON | 含义 |
|---|---|---|
| 1 | `{"working": true}` 或 `{"status": "working"}` | 开始处理 |
| 2 | `{"heartbeat": {"elapsed": 12}}` | 心跳，秒数 |
| 3 | `{"image": {"record_id":1,"turn_id":"...","index":0,"url":"...","ai_generated":true}}` | 配图完成 |
| 4 | `{"image_failed": {"record_id":1,"turn_id":"...","indexes":[0]}}` | 配图失败 |
| 5 | `{"token": "..."}` | 流式文本增量 |
| 6 | `{"structuring": true}` | 进入结构化收口阶段 |
| 7 | `{"answer": { /* ChefAnswer */ }}` | 结构化最终答案 |
| 8 | `{"stage": "thinking"}` | 阶段提示 |
| 9 | `{"finish": true, "session_id": "...", "record_id": 12}` | 本轮结束 |
| 10 | `{"error": "..."}` | 错误，前端应抛异常 |

**两个必须照抄的细节**：

1. **token 过滤**：`token` 字段需 `trim()` 后判断，若以 `{` 开头且 `}` 结尾、或以 `[` 开头且 `]` 结尾，则**丢弃该 token**（这是过滤模型吐出的 JSON 碎片，照抄否则界面会闪出原始 JSON）。
2. `stage` 的取值：`thinking` / `writing` / `searching` / `auditing` / `generating_image` / `structuring` / `switching_model`。

---

## 4. 端点总表（52 个）

图例：**信封** = `{code,messages,data}`；**裸返** = 直接 JSON；**SSE** = 事件流；`★` = `client.ts` 已提供调用封装。封装不等于页面已经接入；页面位置统一待定。

### chat_route.py（3）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/` ★ | 信封 | — | `{code:200, messages:"服务正常", data:null}`（根路由自检） |
| POST | `/api/chat/cancel-image` ★ | 信封 | Form: `session_id`(必)、`turn_id`(可)、`keep_text`(`1`/`0`) | `data:{session_id, turn_id, record_id, keep_text}` |
| POST | `/api/chat` ★ | **SSE** | 见第 3 节 | 事件流 |

### session_route.py（12）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/sessions` ★ | 裸返 | — | `{sessions:[Session]}` |
| POST | `/api/sessions` ★ | 裸返 | — | `{session:Session}` |
| PATCH | `/api/sessions/{sid}` ★ | 裸返 | JSON `{title}`（≤40 字） | `{ok:true, msg}` |
| DELETE | `/api/sessions/{sid}` ★ | 裸返 | — | `{ok:true, msg}` |
| POST | `/api/sessions/{sid}/clear` ★ | 裸返 | — | `{ok:true, msg}` |
| POST | `/api/sessions/{sid}/messages` ★ | 裸返 | Form: `user_text`、`answer`、`time`、`image_name`、`image_type`、`image` | `{ok:true, msg}`（兼容入口） |
| DELETE | `/api/sessions/{sid}/messages/{msg_id}` ★ | 裸返 | — | `{ok:true, msg}` |
| POST | `/api/sessions/{sid}/messages/{rec_id}/feedback` ★ | 信封 | JSON `{rating:"up"\|"down"}` | `data:{feedback}` |
| POST | `/api/sessions/{sid}/messages/{rec_id}/star` ★ | 信封 | JSON `{starred:bool}` | `data:{starred}` |
| GET | `/api/favorites` ★ | 信封 | — | `data:{favorites:[FavoriteItem]}` |
| GET | `/api/feedback/weekly` ★ | 信封 | — | `data:{up, down, total, down_dishes, down_items}` |
| POST | `/api/feedback/forget-dish` ★ | 信封 | JSON `{dish}`（1–40 字） | `data:{removed, dish}` |

### preferences_route.py（18）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/preferences` ★ | 信封 | — | `data:{preferences:string}` |
| PUT | `/api/preferences` ★ | 信封 | JSON `{preferences}`（≤5000 字） | `data:{preferences}` |
| GET | `/api/profile` ★ | 信封 | — | `data:{exists:bool, family:FamilyData}` |
| PUT | `/api/profile` ★ | 信封 | JSON `HealthProfilePayload` | `data:{exists, family}`（兼容入口，更新激活成员） |
| POST | `/api/profile/members` ★ | 信封 | JSON `{name, profile}` | `data:{exists, family}` |
| PUT | `/api/profile/members/{member_id}` ★ | 信封 | JSON `{name, profile}` | `data:{exists, family}` |
| DELETE | `/api/profile/members/{member_id}` ★ | 信封 | — | `data:{exists, family}` |
| DELETE | `/api/profile/member/{member_id}` ★ | 信封 | — | 同上（**单数别名路由**） |
| PUT | `/api/profile/active` ★ | 信封 | JSON `{member_id}` | `data:{exists, family}` |
| GET | `/api/profile/export` ★ | 信封 | — | `data:{export:{app,version,exported_at,active_id,members}}` |
| POST | `/api/profile/import` ★ | 信封 | JSON `{members:[1..8], meta}` | `data:{exists, family}` |
| POST | `/api/profile/dislikes/add` ★ | 信封 | JSON `{item(1-12字), member_id?}` | `data:{added:bool, dislikes}` |
| POST | `/api/profile/taste-note` ★ | 信封 | JSON `{text(1-20字)}` | `data:{added:bool, taste_notes}` |
| GET | `/api/profile/taste-suggestion` ★ | 信封 | — | `data:{suggestion}` |
| GET | `/api/preferences/candidates/pending` ★ | 信封 | Query `session_id`(可) | `data:{candidates:[MemoryCandidate]}` |
| POST | `/api/preferences/candidates/{id}/confirm` ★ | 信封 | — | `data:{candidate}` |
| POST | `/api/preferences/candidates/{id}/once` ★ | 信封 | — | `data:{candidate}` |
| POST | `/api/preferences/candidates/{id}/dismiss` ★ | 信封 | — | 仅 `{code,messages}`，**无 data** |

### reports_route.py（5）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/reports/member-weekly` ★ | 裸返 | Query `name` | 某位成员近 7 天周报；无记录时 `has_data:false` |
| GET | `/api/reports/meal-attribution` ★ | 裸返 | — | `{total,attributed,unattributed,by_member,period,attributed_this_week,unattributed_this_week}` |
| GET | `/api/reports/weekly` ★ | 裸返 | — | `WeeklyReport`：`{has_data, meals, top_dishes, lights, light_trends, guardrail_triggers, range, recommendations, feedback_summary}`；无数据时 `{has_data:false, message}` |
| POST | `/api/reports/feedback` ★ | 裸返 | JSON（自由 dict，前端传 `{dish, rating, tags, comment}`） | `{saved:true, count}` |
| GET | `/api/reports/weekly-summary` ★ | 裸返 | Query `refresh`(bool) | 成功 `{ai_summary, cached}`；失败 `{ai_summary:null, reason:"no_data"\|"empty"\|"llm_failed:..."}` |

### service_route.py（2）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/service/vision` ★ | 信封 | — | `data:ServiceVision` |
| POST | `/api/service/preview` ★ | 信封 | JSON `ServicePreviewRequest` | `data:ServicePreviewResult` |

### nearby_route.py（3）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| GET | `/api/nearby` ★ | 信封 | Query: `query`、`city`、`district`、`budget`(默认50)、`location`、`radius`(默认1500)、`page`(默认1) | `data:NearbyResult` |
| GET | `/api/nearby/map` ★ | 信封 | 与 `/api/nearby` 相同 | `data:{source,amap_configured,coordinate_system:"gcj02",center,origin,markers,warning,allergen_filter?}` |
| GET | `/api/location/resolve` ★ | 信封 | Query `location` | `data:ResolvedLocation` |

### fridge_route.py（4）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| POST | `/api/fridge/vision` ★ | 裸返 | Form `image`(文件) | `{items:[{name,quantity}], draft:true, note}` |
| GET | `/api/fridge` ★ | 裸返 | — | `{items:[...]}` |
| POST | `/api/fridge/set` ★ | 裸返 | Form `items` | 写入后的权威清单 `{items:[...]}` |
| POST | `/api/fridge/add` ★ | 裸返 | Form `items` | 写入后的权威清单 `{items:[...]}` |

### speech_route.py（1）

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| POST | `/api/transcribe` ★ | 信封 | Form `audio`(文件) | 成功 `{code:200, data:{text, provider:"dashscope", available:true}}`；未配置 `{code:503, data:{...,available:false}}` |

### health_route.py（3）· 运维用

| 方法 | 路径 | 形态 | 响应要点 |
|---|---|---|---|
| GET | `/api/health/live` ★ | 裸返 | `{status:"ok"}` |
| GET | `/api/health/ready` ★ | 裸返 | 就绪 200 / 未就绪 503，body 为 `{status,checks,rag,time}` |
| GET | `/api/health/status` ★ | 裸返 | `{status,startup:{knowledge_base,uptime_seconds,...}}` |

### kb_route.py（1）· 评测用

| 方法 | 路径 | 形态 | 入参 | 响应要点 |
|---|---|---|---|---|
| POST | `/api/kb/search` ★ | 裸返 | JSON `{query, k(默认5,上限20)}` | `{query, count, hits:[{text, distance}]}` |

---

## 5. 请求模型（Pydantic，服务端强校验）

| 模型 | 字段（类型 / 约束） |
|---|---|
| `PreferencesPayload` | `preferences: str`（≤5000） |
| `BasicInfo` | `height_cm`(30–260)、`weight_kg`(2–500)、`age`(0–120)、`sex`(`""`/`male`/`female`/`other`) |
| `HealthProfilePayload` | `basic`、`conditions[≤24]`、`allergens[≤24]`、`restricts[≤24]`、`goal(≤40)`、`diet_style(≤40)`、`dislikes[≤60]`、`taste_notes[≤12]` |
| `MemberPayload` | `name: str`（1–20）、`profile: HealthProfilePayload` |
| `ActiveMemberPayload` | `member_id: str`（1–40） |
| `DislikeAddPayload` | `item: str`（1–12）、`member_id: str \| None` |
| `TasteNotePayload` | `text: str`（1–20） |
| `ImportPayload` | `members: List[MemberPayload]`（1–8）、`meta: str`（≤200） |
| `FeedbackPayload` | `rating: str`（正则 `^(up\|down)$`） |
| `RenamePayload` | `title: str`（≤40） |
| `StarPayload` | `starred: bool` |
| `ForgetDishPayload` | `dish: str`（1–40） |
| `ServicePreviewRequest` | `recipe_name`(必)、`inventory_text`、`image_url`、`mode`(`home_chef`/`voice`/`text`)、`expected_ingredients` |
| `KbSearchRequest` | `query: str`、`k: int`（默认 5） |

---

## 6. 响应数据模型（TS 类型，可直接复用）

共享定义在 `frontend/web/src/types.ts`，接口专用的响应类型直接与 `client.ts` 中的封装放在一起。重建时的核心几个：

```ts
type DecisionMode = 'home' | 'dining' | 'health'

interface Session { session_id: string; title?: string; created_at?: string; messages?: SessionMessage[] }

interface SessionMessage {
  id: number; user_text: string; answer: string; time?: string
  image_name?: string | null; image_type?: string | null
  image_url?: string | null; user_image_url?: string | null
  cancelled?: boolean; image_cancelled?: boolean
  starred?: boolean; feedback?: 'up' | 'down' | null
}

interface Recipe {
  name: string; intro: string; difficulty: number; nutrition: number
  seasonings: { name: string; amount: string }[]
  steps: string[]; image_url: string | null
  image_ai_generated: boolean; image_note?: string
}

interface ChefAnswer {
  opening?: string
  recipes: Recipe[]
  image_url?: string | null; image_ai_generated?: boolean
  image_requested?: boolean; image_note?: string
  chef_tip?: string
  sources?: { source: string; section?: string; snippet?: string; category?: string }[]
  guardrails?: { condition: string; rule?: string; status: string; reason?: string }[]
  health_lights?: { label: string; level: 'green' | 'yellow' | 'red'; reason?: string }[]
  member_adjustments?: string[]
  dish_matrix?: { dish: string; member: string; verdict: '可吃' | '需调整' | '待确认' | '不可吃'; reason?: string }[]
  primary_member?: string
}

interface FamilyData { version: number; active_id: string; members: FamilyMember[] }
interface FamilyMember { id: string; name: string; profile: MemberProfile }

interface WeeklyReport {
  has_data: boolean; message?: string; meals?: number
  top_dishes?: [string, number][]; lights?: Record<string, number>
  light_trends?: Record<string, string>; guardrail_triggers?: number
  range?: [string, string]; recommendations?: string[]
  feedback_summary?: { count: number; tags: Record<string, number> }
}

interface FavoriteItem {
  sid: string; rec_id: number; session_title: string
  user_text: string; dish: string
  image_url?: string | null; answer?: ChefAnswer | null
}

interface NearbyResult {
  source: string; amap_configured: boolean
  restaurants: { name: string; cuisine: string; avg_price: number | null
                 distance_km?: number | null; address?: string; guardrail?: string }[]
  warning?: string
}
```

---

## 7. 重建前端要点清单

1. **先封装一个 `request()`**，但要能区分三种响应形态——建议按端点显式声明 `unwrap: 'envelope' | 'raw'`，别指望统一拦截器。
2. **错误读取**：优先读 `detail`，其次 `messages`，最后回落到 `HTTP {status}`；后端返回的 `detail` 可能是 HTML（nginx 兜底页），建议剥离标签并截断。
3. **`/api/chat` 不能用 `EventSource`**，因为要 POST + FormData。必须用 `fetch` + `response.body.getReader()` + `TextDecoder` 手工切 `\n\n`（照抄第 3 节逻辑）。
4. **`want_image` / `keep_text` 传字符串 `"1"`/`"0"`**，不是布尔。
5. **`mode` 的三个取值**：`home` / `dining` / `health`。
6. **布尔字段命名不统一**：裸返端点用 `ok`，信封端点用 `code`，判断成功不要只看 HTTP 200（`/api/transcribe` 用 `code:503` 表达"未配置"）。
7. **`GET /api/reports/weekly` 无数据时**返回 `{has_data:false}`，不是 404，前端要单独处理空态。
8. **图片相关有两个地址字段**：`image_url`（AI 生成配图）与 `user_image_url`（用户上传），别混用。
9. **配图是异步的**：`answer` 事件先到，`image` 事件后到（可能几十秒后），期间用 `turn_id` 可调 `/api/chat/cancel-image` 取消；`image_failed` 表示失败。
10. **现有前端的已知不一致**：
    - `ChatArea.tsx:196` 直接裸 `fetch('/api/reports/weekly')`，绕过了 client.ts 封装；
    - `addTasteNote` 的 JSON `Content-Type` 已在 2026-10-18 的接口层补齐。

---

## 8. 前端接口层覆盖

2026-10-18 以当前后端为基线，`frontend/web/src/api/client.ts` 已补齐此前缺失的 10 个调用：

| 封装 | 后端接口 | 用途 |
|---|---|---|
| `fetchApiHealth()` | `GET /api/` | 根路由自检 |
| `appendSessionMessage()` | `POST /api/sessions/{sid}/messages` | 兼容手动补消息 |
| `fetchNearbyMap()` | `GET /api/nearby/map` | 地图中心点、坐标与 marker |
| `replaceActiveProfile()` | `PUT /api/profile` | 兼容整档写入激活成员 |
| `deleteMemberLegacy()` | `DELETE /api/profile/member/{member_id}` | 旧前端单数路径别名 |
| `fetchMealAttribution()` | `GET /api/reports/meal-attribution` | 成员归属只读诊断 |
| `fetchLiveness()` | `GET /api/health/live` | 进程存活探针 |
| `fetchReadiness()` | `GET /api/health/ready` | 依赖就绪探针，保留 503 业务载荷 |
| `fetchHealthStatus()` | `GET /api/health/status` | 启动阶段与知识库预热状态 |
| `searchKnowledgeBase()` | `POST /api/kb/search` | 知识库检索/评测 |

“全部有接口”不等于“全部直接进用户主界面”。地图、服务预览、家庭导入导出可进入业务页面；健康探针、归属诊断和知识库检索更适合作为连接状态或管理/调试入口。页面位置仍待业务确认，本轮没有修改任何 UI 入口。

---

*本文档的路由数量与参数以运行时 `app.openapi()` 为权威来源。项目大量端点未声明 `response_model`，因此响应结构以路由实现和前端 TypeScript 类型交叉核对。*
