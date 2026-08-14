# 小膳管家 · 前端（frontend/web）

React 19 + Vite + TypeScript 前端工程，对接后端 FastAPI（默认 8010 端口）。

当前界面采用「膳食决策工作台」信息架构：

- 膳食决策：流式对话、图片输入、浏览器录音转文字、结构化菜谱。
- 健康决策链：持续展示后端注入的健康护栏和知识库依据。
- 会话管理：搜索、新建、删除、清空，以及删除单轮问答。
- 私厨预演：调用真实的文字食材缺口接口，并明确展示尚未接入的预约/支付边界。
- 响应式布局：桌面三栏、平板收起证据栏、移动端抽屉导航。

## 目录结构

```text
frontend/web
├── src/
│   ├── api/client.ts          # API 客户端：会话 CRUD + /api/chat SSE 流式解析
│   ├── components/
│   │   ├── SessionSidebar.tsx # 产品导航、会话搜索与历史记录
│   │   ├── ChatArea.tsx       # 对话、场景选择、图像与语音输入
│   │   ├── RecipeCard.tsx     # 后厨出单式结构化菜谱
│   │   ├── InsightPanel.tsx   # 健康护栏与权威依据侧栏
│   │   ├── ServicePreview.tsx # 上门私厨食材缺口预演
│   │   └── Icon.tsx           # 无外部依赖的 SVG 图标集
│   ├── types.ts               # 与后端接口对齐的类型定义
│   ├── App.tsx                # 页面状态与布局
│   └── main.tsx
├── vite.config.ts             # /api 代理 -> http://127.0.0.1:8010
└── index.html
```

## 快速开始

1. 启动后端（仓库根目录）：`python run.py`，后端监听 `http://127.0.0.1:8010`
2. 安装前端依赖：`npm install`
3. 启动开发服务器：`npm run dev`，访问 `http://localhost:5173`
4. 构建产物：`npm run build`

## 已对接接口

- `GET /api/`：健康检查
- `POST /api/chat`：multipart 表单（`session_id`、`message`、可选 `image`），SSE 流式返回
  - 事件：`working` / `heartbeat` / `token` / `structuring` / `answer` / `finish` / `error`
  - `answer` 为 ChefAnswer JSON（recipes / guardrails / sources）
- `GET /api/sessions`、`POST /api/sessions`：会话列表 / 新建
- `DELETE /api/sessions/{sid}`：删除会话
- `POST /api/sessions/{sid}/clear`、`DELETE /api/sessions/{sid}/messages/{msg_id}`：清空 / 删单条
- `GET /api/service/vision`、`POST /api/service/preview`：上门私厨演示
- `POST /api/transcribe`：语音转文字

> 后端启动时需要项目现有模型/搜索服务所要求的环境变量，例如
> `TAVILY_API_KEY`。前端即使构建成功，缺少后端密钥时也无法完成真实 AI 对话。
