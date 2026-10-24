# main_app.py：FastAPI 总入口。只做三件事：建 app 实例、初始化 DB、挂载子路由。
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request
from storage.sessions import init_db
import os
import threading
import logging
import time
import uuid

from infrastructure.runtime_logging import configure_logging
from infrastructure.startup_state import set_knowledge_base_status

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
    set_knowledge_base_status("starting")
    try:
        from rag.retriever import get_retriever

        retriever = get_retriever()          # 打开 Chroma 并加载 bge 嵌入模型
        retriever._ensure_bm25()             # 构建 BM25 索引
        # 2026-10-23：预热阶段**不加载**重排模型，两条理由：
        #   ① 报告写的是「重排模型在首次检索时由 CrossEncoder 自动缓存」，
        #      预热不加载才与这句话一致（此前预热多做了一件报告没说要做的重活）；
        #   ② bge-reranker-v2-m3 有 2.2GB，本地未部署，在这里加载等于每次启动都去
        #      试下载、失败再降级 —— 实测白等 12~50 秒，是 72.4s 预热里的大头。
        # 真要重排就设 KB_ENABLE_RERANK=1，它会在首次检索时惰性加载（见 retriever._rerank_enabled）。
        retriever.store.search("知识库预热", n_results=1)  # 走一次真实向量检索
        set_knowledge_base_status("ready")
        print(f"[warmup] 知识库预热完成，用时 {time.time() - started:.1f}s（reranker={'on' if retriever._reranker else 'off'}）", flush=True)
    except Exception as exc:  # 预热失败不阻塞服务启动，首次提问时再惰性加载
        set_knowledge_base_status("degraded", str(exc))
        print(f"[warmup] 知识库预热失败，知识库降级为仅文本检索（将在首次提问时重试）：{exc}", flush=True)


def _start_knowledge_base_warmup() -> None:
    """在 FastAPI 启动阶段再预热知识库，避免模块导入被重依赖拖慢。"""
    threading.Thread(
        target=_warmup_knowledge_base,
        name="kb-warmup",
        daemon=True,
    ).start()


@app.on_event("startup")
def _startup_tasks() -> None:
    _start_knowledge_base_warmup()
    _start_checkpoint_prune()


# —— 断点库孤儿清理：删会话只删 sessions/*.json，checkpoint.db 里的 thread 得单独收 ——
# 关掉：CHEF_CHECKPOINT_PRUNE=0。清理对象只有「磁盘上已经没有对应会话文件」的线程，
# 启动期没有任何在跑的轮次，不会误删活跃会话。延迟 20s 让预热线程先跑完。
_CHECKPOINT_PRUNE_DELAY_S = 20


def _prune_orphan_checkpoints() -> None:
    import time
    import sys
    from pathlib import Path

    time.sleep(_CHECKPOINT_PRUNE_DELAY_S)
    # 关停竞态：本线程是 daemon，若在解释器 finalize 时才醒来去写 stdout，
    # 会和主线程抢 stdout 缓冲锁，直接 Fatal Python error（实测 pytest 收尾崩）。
    # 日志走 logging（落盘 handler），且收尾阶段直接放弃这轮清理——下次启动会重跑。
    if sys.is_finalizing():
        return
    logger = logging.getLogger("checkpoint")
    try:
        from storage import checkpoints
        from storage.sessions import SESSIONS_DIR

        sessions_dir = Path(SESSIONS_DIR)
        if not sessions_dir.exists():
            # 目录都读不到时不做任何删除，避免把「路径配置错了」当成「会话都没了」。
            return
        active = {p.stem for p in sessions_dir.glob("*.json")}
        stats = checkpoints.purge_orphans(active)
        if stats["deleted"]:
            checkpoints.vacuum()
            logger.info(
                "清理孤儿断点线程 %d/%d 条，保留活跃会话 %d 个",
                stats["deleted"],
                stats["threads"],
                len(active),
            )
        else:
            logger.info("断点库无孤儿线程（共 %d 条）", stats["threads"])
    except Exception as exc:  # 清理失败绝不能影响服务启动
        logger.warning("孤儿断点清理跳过：%s", exc)


def _start_checkpoint_prune() -> None:
    if (os.getenv("CHEF_CHECKPOINT_PRUNE", "1") or "").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return
    threading.Thread(
        target=_prune_orphan_checkpoints,
        name="checkpoint-prune",
        daemon=True,
    ).start()


# —— 失败图自动补图队列：网络抖动期没能出图的菜，后台每 10 分钟扫一轮补上 ——
from services.image_retry_queue import start_retry_daemon

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
from api.routes.weight_route import router as weight_router
app.include_router(weight_router, prefix="/api")
from api.routes.fridge_route import router as fridge_router
app.include_router(fridge_router, prefix="/api")
from api.routes.health_route import router as health_router

app.include_router(health_router, prefix="/api")

# 评测流程复用后端 retriever（/api/kb/search），避免外部脚本独占 Chroma 锁
from api.routes.kb_route import router as kb_router

app.include_router(kb_router, prefix="/api")

# 语音识别路由：POST /api/transcribe（不碰 Agent 主逻辑，只在前后端之间加“语音转文字”）
from api.routes.speech_route import router as speech_router

app.include_router(speech_router, prefix="/api")
