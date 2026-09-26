# 按需求找代码

| 想修改什么 | 先看 | 再看 |
|---|---|---|
| 更换主模型 | `infrastructure/model_name.py` | `infrastructure/configs.py`、`.env` |
| 修改答案字段 | `agent/schemas.py` | `agent/chains.py`、`frontend/web/src/types.ts` |
| 修改模型回答风格 | `agent/prompts.py` | `agent/graph.py` |
| 修改健康红线 | `domain/nutrition_rules.py` | `domain/allergen_rules.py` |
| 修改画像约束 | `domain/constraint_rules.py` | `api/routes/preferences_route.py` |
| 修改长期记忆确认 | `storage/memory_candidates.py` | `api/routes/preferences_route.py` |
| 修改 Agent 分支 | `agent/graph.py` | `main.py`、`agent_tools/legacy.py` |
| 修改 RAG | `rag/` | `scripts/build_kb_rag.py`、`.env` |
| 修改聊天 SSE | `api/routes/chat_route.py` | `frontend/web/src/api/client.ts` |
| 修改聊天页面 | `ChatArea.tsx` | `App.tsx`、`index.css` |
| 修改菜谱卡片 | `RecipeCard.tsx` | `types.ts` |
| 修改家庭画像页面 | `FamilyPanel.tsx` | `api/client.ts`、`preferences_route.py` |
| 修改附近餐厅查询 | `api/routes/nearby_route.py` | `agent_tools.py` |
| 修改地图标记 | `NearbyRestaurantMap.tsx` | `index.css` |
| 修改导航链接 | `nearbyNavigation.ts` | `NearbyRestaurantMap.tsx` |
| 修改会话保存 | `storage/sessions.py` | `api/routes/session_route.py` |
| 修改图片生成 | `services/image_gen.py` | `services/image_retry_queue.py`、`storage/dish_assets.py` |
| 修改冰箱 | `fridge_route.py` | `ChatArea.tsx` |
| 修改周报 | `reports_route.py` | `WeeklyReportPage.tsx` |
| 修改启动方式 | `run.py` | `frontend/web/vite.config.ts` |

## 学习一条完整链路

建议以“用户输入一个饮食问题”为例：

1. `frontend/web/src/components/ChatArea.tsx`
2. `frontend/web/src/api/client.ts`
3. `api/routes/chat_route.py`
4. `main.py`
5. `agent/graph.py`
6. `agent/schemas.py`
7. `api/routes/chat_route.py` 的 SSE
8. `frontend/web/src/api/client.ts` 的事件解析
9. `RecipeCard.tsx` 和 `InsightPanel.tsx`
