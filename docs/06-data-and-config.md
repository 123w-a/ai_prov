# 数据与配置

## 运行数据

| 路径 | 内容 | 特点 |
|---|---|---|
| `data/profile.json` | 家庭画像 | 隐私，长期保存 |
| `data/preferences.txt` | 旧版自由文本偏好 | 隐私，兼容用途 |
| `data/memory_candidates.json` | 待确认记忆 | 隐私，候选状态 |
| `data/meals.json` | 饮食记录 | 运行数据 |
| `data/pantry_log.json` | 冰箱和库存 | 运行数据 |
| `data/taste_signals.json` | 口味反馈信号 | 运行数据 |
| `data/dish_assets.json` | 菜品图片资产 | 可重建/运行数据 |
| `data/weekly_summary.json` | 周报 | 可重建 |
| `sessions/` | 会话记录 | 隐私 |
| `resources/checkpoint.db*` | LangGraph 状态 | 可重建运行态 |
| `resources/uploads/` | 上传文件 | 运行态 |

`.gitignore` 已将多数个人数据、缓存、数据库、日志和构建产物排除。不要为了“看起来完整”把这些文件提交到仓库。

## 后端配置

模板：`.env.example`

- 模型：`DEEPSEEK_*`、`CHAT_*`、`CHEF_PROVIDER`；
- 视觉和图片：`QWEN_*`、`DASHSCOPE_API_KEY`；
- 联网搜索：`TAVILY_API_KEY`；
- 高德后端：`AMAP_KEY`；
- OSS：`OSS_*`；
- RAG：`KB_*`。

## 前端配置

模板：`frontend/web/.env.example`

```dotenv
VITE_API_BASE=
VITE_AMAP_JS_KEY=
VITE_AMAP_SECURITY_CODE=
```

## Key 的边界

| Key | 使用位置 | 用途 |
|---|---|---|
| `AMAP_KEY` | 后端 `.env` | 高德 Web 服务 POI 和反向地理编码 |
| `VITE_AMAP_JS_KEY` | 前端 `.env` | 浏览器加载高德 JS 地图 |
| `VITE_AMAP_SECURITY_CODE` | 前端 `.env` | 高德 JS 安全配置 |
