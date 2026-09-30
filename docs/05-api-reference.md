# API 参考

所有路由由 `api/main_app.py` 以 `/api` 前缀挂载。

## 聊天

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/chat` | multipart 聊天和 SSE |
| `POST` | `/api/chat/cancel-image` | 取消图片任务 |

常见字段：`session_id`、`message`、`mode`、`want_image`、`image`、`location_context`、`turn_id`。

## 会话

```text
POST   /api/sessions
GET    /api/sessions
PATCH  /api/sessions/{sid}
DELETE /api/sessions/{sid}
POST   /api/sessions/{sid}/clear
DELETE /api/sessions/{sid}/messages/{msg_id}
```

## 家庭画像

```text
GET    /api/preferences
PUT    /api/preferences
GET    /api/profile
PUT    /api/profile
POST   /api/profile/members
PUT    /api/profile/members/{member_id}
DELETE /api/profile/members/{member_id}
PUT    /api/profile/active
POST   /api/profile/dislikes/add
POST   /api/profile/taste-note
GET    /api/profile/taste-suggestion
GET    /api/profile/export
POST   /api/profile/import
```

候选记忆接口位于 `/api/preferences/candidates/...`，支持确认、忽略和仅本次记住。

## 附近餐厅

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/nearby` | 查询餐厅 |
| `GET` | `/api/location/resolve` | 坐标反向解析城市和区县 |

`/api/nearby` 支持 `query`、`city`、`district`、`budget`、`location`、`radius`、`page`。返回的 `source` 用于区分 `amap` 和 mock。

## 其他

```text
GET  /api/health/live
GET  /api/health/ready
POST /api/transcribe
GET  /api/fridge
POST /api/fridge/set
POST /api/fridge/add
POST /api/fridge/vision
GET  /api/service/vision
POST /api/service/preview
GET  /api/reports/weekly
GET  /api/reports/weekly-summary
POST /api/reports/feedback
```
