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

下面的命令都**在仓库根目录**执行，路径一律相对，不依赖盘符。

### 1. 装依赖

Python 依赖用 [uv](https://docs.astral.sh/uv/) 管理，`uv.lock` 已入库，装出来可复现：

```powershell
uv sync
```

没有 uv 就用标准流程：

```powershell
python -m venv .venv
.venv\Scripts\pip install -e .
```

前端依赖：

```powershell
cd frontend\web
npm install
```

### 2. 配置环境变量（这一步漏了后端起不来）

**根目录**和 **frontend/web** 各有一份 `.env.example`，需要分别复制：

```powershell
Copy-Item .env.example .env
Copy-Item frontend\web\.env.example frontend\web\.env
```

根目录 `.env` 里：

- **`TAVILY_API_KEY` 必填。** 未配置时后端在 import 阶段就会报错，进程根本起不来。
- 至少还要配一个对话模型的 Key：`DEEPSEEK_API_KEY`（推荐）或 `CHAT_API_KEY`。
- 其余（语音、视觉、OSS、高德 POI）都是可选项，留空只会让对应功能提示「暂未配置」，不影响启动。

`frontend/web/.env` 可以整个留空：`VITE_API_BASE` 留空时走 Vite 代理。只有要用附近餐厅地图时才需要填 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SECURITY_CODE`。

### 3. 起服务

后端：

```powershell
.venv\Scripts\python.exe run.py
```

前端：

```powershell
cd frontend\web
npm run dev
```

后端默认 `127.0.0.1:8010`，前端通常为 `localhost:5178`（端口被占用时以 Vite 终端输出为准）。
**后端首次冷启动要预热知识库，实测约 74 秒**，这段时间是正常的，别误判为启动失败。

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
- 后端用标准库 `unittest`，不是 pytest；全量回归：`.venv\Scripts\python.exe -m unittest discover tests -v`。
- 后端高德 POI 使用根目录 `.env` 的 `AMAP_KEY`；前端地图使用 `frontend/web/.env` 的 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SECURITY_CODE`。
- 当前没有可复现的文字输出异常时，不修改 `max_tokens`、流式 token 转发、收口、兜底和截断逻辑。


