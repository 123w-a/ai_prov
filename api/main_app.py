# main_app.py：FastAPI 总入口。只做三件事：建 app 实例、初始化 DB、挂载子路由。
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request
from sessions_store import init_db
import os
import threading

app = FastAPI(title="小膳管家")#创键fastapi对象

# 跨域：前端（React 5173 / 本地调试）调用 /api/* 需要放行；同源页面无需跨域也兼容
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 重要：必须在 app 创建后、接收请求前初始化会话目录，否则首次插入会找不到 sessions/ 目录
init_db()


# —— RAG 知识库预热：在启动阶段后台加载向量库、bge 嵌入与重排模型 ——
# 否则首个用户提问要独自承担全部冷启动耗时（实测 30~170 秒），表现为“点了没反应”。
def _warmup_knowledge_base() -> None:
    import time

    started = time.time()
    try:
        from rag.retriever import get_retriever

        retriever = get_retriever()          # 打开 Chroma 并加载 bge 嵌入模型
        retriever._ensure_bm25()             # 构建 BM25 索引
        retriever._ensure_reranker()         # 加载 bge-reranker 重排模型（失败自动跳过）
        retriever.store.search("知识库预热", n_results=1)  # 走一次真实向量检索
        print(f"[warmup] 知识库预热完成，用时 {time.time() - started:.1f}s（reranker={'on' if retriever._reranker else 'off'}）", flush=True)
    except Exception as exc:  # 预热失败不阻塞服务启动，首次提问时再惰性加载
        print(f"[warmup] 知识库预热失败，知识库降级为仅文本检索（将在首次提问时重试）：{exc}", flush=True)


threading.Thread(target=_warmup_knowledge_base, name="kb-warmup", daemon=True).start()


# —— 失败图自动补图队列：网络抖动期没能出图的菜，后台每 10 分钟扫一轮补上 ——
from image_retry_queue import start_retry_daemon

start_retry_daemon()



@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底把未处理异常转成 JSON，避免 nginx 的 HTML 500 直接暴露给前端。"""
    return JSONResponse(status_code=500, content={"code": 500, "messages": "服务内部错误，请稍后重试", "data": None})



# 重要：子路由放在文件末尾导入，避免循环依赖
from api.routes.chat_route import router as chat_router
from api.routes.session_route import router as session_router

# 重要：prefix="/api" 让路由里只写 "/chat/image"，最终对外暴露为 /api/chat/image，
#统一向外面暴露接口
app.include_router(chat_router, prefix="/api")
app.include_router(session_router, prefix="/api")

from api.routes.service_route import router as service_router

from api.routes.nearby_route import router as nearby_router
from api.routes.preferences_route import router as preferences_router

app.include_router(service_router, prefix="/api")
app.include_router(nearby_router, prefix="/api")
app.include_router(preferences_router, prefix="/api")
from api.routes.reports_route import router as reports_router
app.include_router(reports_router, prefix="/api")
from api.routes.fridge_route import router as fridge_router
app.include_router(fridge_router, prefix="/api")

# 语音识别路由：POST /api/transcribe（不碰 Agent 主逻辑，只在前后端之间加“语音转文字”）
from api.routes.speech_route import router as speech_router

app.include_router(speech_router, prefix="/api")
