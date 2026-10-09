"""进程级启动状态，供健康探针和启动日志共享。"""

from __future__ import annotations

import threading
import time
from typing import Any


_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "started_at": time.time(),
    "knowledge_base": "starting",
    "knowledge_base_error": "",
}


def set_knowledge_base_status(status: str, error: str = "") -> None:
    with _LOCK:
        _STATE["knowledge_base"] = status
        _STATE["knowledge_base_error"] = error
        if status == "ready":
            _STATE["knowledge_base_ready_at"] = time.time()


def startup_status() -> dict[str, Any]:
    with _LOCK:
        snapshot = dict(_STATE)
    now = time.time()
    snapshot["uptime_seconds"] = round(max(0.0, now - float(snapshot["started_at"])), 1)
    return snapshot
