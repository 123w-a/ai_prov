# 小膳管家文档中心

> 这里是项目的日常入口。先按学习顺序阅读；需要改功能时，直接打开“按需求找代码”。

## 推荐阅读顺序

| 顺序 | 文档 | 解决的问题 |
|---|---|---|
| 1 | [系统架构](01-architecture.md) | 一次请求从前端到模型再回到页面经过什么 |
| 2 | [后端代码地图](02-backend-map.md) | Python 文件各自负责什么 |
| 3 | [Agent 与 RAG](03-agent-and-rag.md) | LangGraph、护栏、工具和知识库如何协作 |
| 4 | [前端代码地图](04-frontend-map.md) | 页面、组件、类型和 SSE 如何对应 |
| 5 | [API 参考](05-api-reference.md) | 接口按业务怎么查 |
| 6 | [数据与配置](06-data-and-config.md) | 数据保存在哪里，哪些文件不能提交 |
| 7 | [启动与排障](07-run-and-debug.md) | 如何运行、测试和定位常见问题 |
| 8 | [按需求找代码](08-feature-index.md) | 修改一个功能时先看哪些文件 |

## 项目入口

- 后端启动：`run.py`
- FastAPI 总入口：`api/main_app.py`
- Agent 主图：`agent/graph.py`
- 前端入口：`frontend/web/src/App.tsx`
- 前端请求和 SSE：`frontend/web/src/api/client.ts`
- 完整学习稿：桌面上的 `理解.md`

## 阅读约定

- 文档里的路径都是相对于项目根目录 `D:\ai_prvo`。
- 运行时数据、个人画像、会话和密钥不属于代码学习材料。
- 文档与代码冲突时，以代码为准，并更新对应文档。
- 当前没有可复现的文字输出问题时，不修改 `max_tokens`、流式转发、收口、兜底和截断逻辑。
