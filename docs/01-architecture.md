# 系统架构

## 一句话

小膳管家是一个“React 工作台 + FastAPI 接口 + LangGraph Agent + RAG/工具 + 本地持久化”的家庭饮食决策应用。

## 分层

```text
frontend/web
  页面、输入、卡片、地图、SSE 状态
        |
api/
  REST、multipart、SSE、参数校验、错误边界
        |
main.py / agent/graph.py / agent/chains.py
  上下文、LangGraph、工具循环、结构化输出
        |
规则与知识
  domain/nutrition_rules.py
  domain/allergen_rules.py
  domain/constraint_rules.py
  rag/
        |
本地数据
  data/        画像、反馈、饮食记录
  sessions/    聊天展示记录
  resources/   checkpoint、上传和日志
```

## 聊天链路

```text
ChatArea
  -> POST /api/chat
  -> chat_route.py
  -> main.py 组织上下文
  -> agent/graph.py 执行图
  -> 工具 / RAG / 健康护栏
  -> ChefAnswer
  -> SSE: token / answer / image / finish
  -> client.ts
  -> ChatArea / RecipeCard / InsightPanel
```

## 关键边界

| 内容 | 保存位置 | 含义 |
|---|---|---|
| 用户明确保存的家庭画像 | `data/profile.json` | 长期画像 |
| 待确认的偏好候选 | `data/memory_candidates.json` | 不能当作已确认记忆 |
| 会话展示记录 | `sessions/*.json` | 用户可见历史 |
| Agent 中间状态 | `resources/checkpoint.db` | 运行时短期状态 |
| RAG 向量索引 | `kb/chroma/` | 可重建运行资源 |

## 降级能力

- 高德无 Key、断网或无结果：附近餐厅回退 mock，并返回 `source`/`warning`。
- RAG 预热失败：服务仍启动，首次检索再尝试。
- 图片生成失败：文字和结构化答案仍可返回，图片通过失败事件告知前端。
- 语音未配置：接口返回明确不可用状态，不影响文字聊天。
