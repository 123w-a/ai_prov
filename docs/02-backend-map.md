# 后端代码地图

## 入口

| 文件 | 作用 |
|---|---|
| `run.py` | 启动 Uvicorn、监听 `127.0.0.1:8010`、治理端口、固定单 worker |
| `api/main_app.py` | 创建 FastAPI、初始化目录、挂载路由、启动预热任务 |
| `main.py` | 组装用户输入、画像、位置等 Agent 上下文 |

## Agent 和规则

| 文件 | 作用 |
|---|---|
| `agent/graph.py` | LangGraph 状态、节点、条件边、工具循环、checkpointer |
| `agent/chains.py` | 模型链和结构化输出链 |
| `agent/schemas.py` | `ChefAnswer`、菜谱、护栏、来源等数据契约 |
| `agent/prompts.py` | 系统提示词、输出格式和行为约束 |
| `domain/nutrition_rules.py` | 营养与健康规则 |
| `domain/allergen_rules.py` | 过敏原识别和过滤 |
| `domain/constraint_rules.py` | 将画像和请求转换为约束 |
| `storage/memory_candidates.py` | 候选偏好的生成、确认、忽略和一次性使用 |

## 路由

| 文件 | 业务 |
|---|---|
| `api/routes/chat_route.py` | 聊天、SSE、图片取消 |
| `api/routes/session_route.py` | 会话、消息、收藏、反馈 |
| `api/routes/preferences_route.py` | 偏好和家庭画像 |
| `api/routes/nearby_route.py` | 附近餐厅和定位解析 |
| `api/routes/reports_route.py` | 饮食记录、反馈、周报 |
| `api/routes/fridge_route.py` | 冰箱库存和冰箱图片识别 |
| `api/routes/service_route.py` | 上门私厨预演 |
| `api/routes/speech_route.py` | 语音转文字 |
| `api/routes/health_route.py` | 存活和就绪检查 |

## 持久化和外部能力

- `storage/sessions.py`：会话 JSON。
- `storage/utils.py`：原子写入和存储辅助。
- `storage/dish_assets.py`：菜品图片资产。
- `services/image_gen.py`、`services/image_retry_queue.py`：图片生成和失败重试。
- `storage/feedback.py`、`storage/taste.py`：反馈和口味信号。
- `services/oss.py`：对象存储。
- `services/speech.py`：录音文件识别。
- `services/vision.py`：视觉能力。
