"""进程存活与依赖就绪探针。

readiness 只做本地、快速、无模型的检查，不访问外网，也不回显任何密钥。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from configs import KB_CONFIG
from rag.store import resolve_project_path


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = PROJECT_ROOT / "sessions"


def _dir_writable(path: Path) -> bool:
    """用真实临时文件探测目录可写，避免 os.access 的假阳性。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".health-", delete=True):
            return True
    except Exception:
        return False


def _model_key_configured() -> bool:
    return any(
        str(os.getenv(name) or "").strip()
        for name in ("CHAT_API_KEY", "DEEPSEEK_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
    )


def _rag_index_status() -> tuple[bool, str]:
    path = resolve_project_path(KB_CONFIG.get("kb_dir", "kb/chroma"))
    sqlite_path = path / "chroma.sqlite3"
    if not path.is_dir():
        return False, "知识库目录不存在"
    if not sqlite_path.is_file() or sqlite_path.stat().st_size <= 0:
        return False, "Chroma 索引文件不存在或为空"
    return True, "ok"


def readiness_payload() -> tuple[bool, dict]:
    checks = {
        "data_dir": _dir_writable(DATA_DIR),
        "sessions_dir": _dir_writable(SESSIONS_DIR),
        "model_key": _model_key_configured(),
    }
    rag_ok, rag_reason = _rag_index_status()
    checks["rag_index"] = rag_ok
    payload = {
        "status": "ok" if all(checks.values()) else "not_ready",
        "checks": checks,
        "rag": rag_reason,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    return all(checks.values()), payload


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready():
    ok, payload = readiness_payload()
    return JSONResponse(status_code=200 if ok else 503, content=payload)
