# 小膳管家

家庭饮食决策 AI Agent：React 前端、FastAPI 服务、LangGraph 编排、RAG 知识库和健康护栏。

## 从哪里开始

| 目的 | 入口 |
|---|---|
| 快速运行 | [docs/07-run-and-debug.md](docs/07-run-and-debug.md) |
| 先理解架构 | [docs/01-architecture.md](docs/01-architecture.md) |
| 按文件学习后端 | [docs/02-backend-map.md](docs/02-backend-map.md) |
| 理解 Agent 和 RAG | [docs/03-agent-and-rag.md](docs/03-agent-and-rag.md) |
| 学习前端 | [docs/04-frontend-map.md](docs/04-frontend-map.md) |
| 查询接口 | [docs/05-api-reference.md](docs/05-api-reference.md) |
| 查配置和数据 | [docs/06-data-and-config.md](docs/06-data-and-config.md) |
| 修改功能找代码 | [docs/08-feature-index.md](docs/08-feature-index.md) |

## 快速启动

后端：

```powershell
D:\ai_prvo\.venv\Scripts\python.exe run.py
```

前端：

```powershell
cd D:\ai_prvo\frontend\web
npm run dev
```

后端默认 `127.0.0.1:8010`，前端通常为 `localhost:5178`。如果前端端口被占用，以 Vite 终端输出为准。

## 主要目录

```text
api/             FastAPI 路由
frontend/web/    React 前端
rag/             RAG 检索
agent_tools/     Agent 工具包
kb/              知识库资料和索引
data/            本地运行数据
resources/       checkpoint、上传和运行资源
sessions/        会话数据
tests/           unittest 测试
docs/            项目文档和代码导航
```

## 重要说明

- `.env`、家庭画像、会话、日志、数据库和模型缓存属于本地运行数据，不要提交。
- 后端高德 POI 使用根目录 `AMAP_KEY`；前端地图使用 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SECURITY_CODE`。
- 当前没有可复现的文字输出异常时，不修改 `max_tokens`、流式 token 转发、收口、兜底和截断逻辑。
