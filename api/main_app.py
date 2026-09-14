# main_app.py：FastAPI 总入口。只做三件事：建 app 实例、初始化 DB、挂载子路由。
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request
from sessions_store import init_db
import os
import threading
import logging
import time
import uuid

from runtime_logging import configure_logging

configure_logging()

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
    """兜底记录异常并返回统一 JSON；堆栈只进后端日志。"""
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
    started = getattr(request.state, "started_at", time.perf_counter())
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logging.getLogger("api.unhandled").exception(
        "request_id=%s path=%s method=%s elapsed_ms=%d",
        request_id,
        request.url.path,
        request.method,
        elapsed_ms,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "messages": "服务内部错误，请稍后重试",
            "data": None,
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.middleware("http")
async def request_observability(request: Request, call_next):
    """给每个请求分配可追踪 ID，供异常日志和前端排障关联。"""
    request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.started_at = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


# 请求体总量粗筛：按 Content-Length 先挡一层，别让超大 body 进到解析阶段。
# 注意这只是粗筛——Content-Length 可以缺失或撒谎，所以各上传端点还会按真实字节再卡一次。
_MAX_REQUEST_BYTES = int(os.getenv("CHEF_MAX_REQUEST_MB", "16")) * 1024 * 1024


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_REQUEST_BYTES:
        request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]
        return JSONResponse(
            status_code=413,
            content={
                "code": 413,
                "messages": (
                    f"请求体超过 {_MAX_REQUEST_BYTES // (1024 * 1024)}MB 上限，"
                    "请压缩图片或减少上传内容"
                ),
                "data": None,
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )
    return await call_next(request)



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

from api.routes.health_route import router as health_router

app.include_router(health_router, prefix="/api")

# 语音识别路由：POST /api/transcribe（不碰 Agent 主逻辑，只在前后端之间加“语音转文字”）
from api.routes.speech_route import router as speech_router

app.include_router(speech_router, prefix="/api")
