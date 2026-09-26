# 前端代码地图

## 页面结构

| 文件 | 作用 |
|---|---|
| `frontend/web/src/App.tsx` | 页面总状态和布局 |
| `components/SessionSidebar.tsx` | 会话导航和历史 |
| `components/ChatArea.tsx` | 输入、对话、附近餐厅和流式状态 |
| `components/RecipeCard.tsx` | 结构化菜谱 |
| `components/InsightPanel.tsx` | 健康护栏和知识依据 |
| `components/FamilyPanel.tsx` | 家庭成员画像 |
| `components/NearbyRestaurantMap.tsx` | 高德地图和红色编号标记 |
| `components/ServicePreview.tsx` | 私厨预演 |
| `components/WeeklyReportPage.tsx` | 周报 |

## 数据和通信

- `api/client.ts`：所有主要 HTTP 请求和 `/api/chat` SSE 解析。
- `types.ts`：前后端数据类型。
- `utils/richText.tsx`：正文展示。
- `utils/sourceFormat.ts`：来源格式化。
- `utils/nearbyNavigation.ts`：坐标转换和高德导航 URI。

## SSE 事件

当前前端会处理：

```text
working
heartbeat
token
stage
structuring
answer
image
image_failed
finish
error
```

不要只用 `token` 判断请求完成。`answer` 负责结构化卡片，`image` 可能晚于文字，`finish` 才表示本轮结束。

## 地图配置

前端 `frontend/web/.env`：

```dotenv
VITE_AMAP_JS_KEY=...
VITE_AMAP_SECURITY_CODE=...
```

这两个配置只负责浏览器地图。后端 POI 查询使用根目录 `.env` 的 `AMAP_KEY`，两者不能互换。
