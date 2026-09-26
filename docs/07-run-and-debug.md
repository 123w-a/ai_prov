# 启动与排障

## 启动后端

```powershell
D:\ai_prvo\.venv\Scripts\python.exe run.py
```

默认地址：

- `http://127.0.0.1:8010/api/`
- `http://127.0.0.1:8010/api/health/live`
- `http://127.0.0.1:8010/docs`

## 启动前端

```powershell
cd D:\ai_prvo\frontend\web
npm install
npm run dev
```

通常访问 `http://localhost:5178/`。端口被占用时，以 Vite 终端实际输出为准。

## 测试和构建

```powershell
cd D:\ai_prvo\frontend\web
npm run build
```

```powershell
cd D:\ai_prvo
D:\ai_prvo\.venv\Scripts\python.exe -m unittest discover tests -v
```

项目使用标准库 `unittest`，不是 pytest。

## 排障顺序

### 页面打不开

1. 确认前端 Vite 进程仍在；
2. 确认访问的是终端打印的端口；
3. 查看浏览器控制台和 Network。

### 聊天失败

1. 请求 `GET /api/health/live`；
2. 检查根目录 `.env` 的模型 Key 和 `TAVILY_API_KEY`；
3. 查看 `/api/chat` 的 SSE 是否出现 `error`；
4. 对照后端日志中的 request id；
5. 不要在没有复现证据时调整文字输出链路。

### 地图没有底图

1. 检查前端两个高德 JS 配置；
2. 修改 `.env` 后重启 Vite；
3. 检查浏览器是否能加载高德 Loader；
4. 后端 `AMAP_KEY` 只负责 POI，不负责底图。

### 餐厅是模拟数据

检查返回值中的 `source` 和 `warning`。`mock`/`mock_empty` 表示没有使用到有效的高德实时结果，继续检查 Key、网络、配额、城市和坐标。

### 首次提问很慢

RAG 可能正在加载 embedding 或 reranker。先看后端 `[warmup]` 日志和健康接口，不要把冷启动误判为 SSE 截断。
