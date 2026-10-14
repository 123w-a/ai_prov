"""应用日志配置：错误日志落盘并按大小轮转。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

from infrastructure.paths import DATA_DIR, LOG_DIR


_CONFIG_LOCK = Lock()
_CONFIGURED = False
# 日志写到项目之外的用户级目录（见 infrastructure/paths.py 的 LOG_DIR 说明）
LOG_PATH = LOG_DIR / "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


def _build_file_handler(formatter: logging.Formatter) -> RotatingFileHandler | None:
    """建文件 handler；首选位置写不进去就往项目内退一级，都不行就当没有。

    2026-10-14：只落盘、不留兜底，会让"写日志"把主链路带崩——本模块自己的约定
    是**日志失败一律静默**（见 ``paths.LOG_DIR`` 的文档）。``paths`` 那边现在会
    真写一次来判可写，但目录可写 ≠ 目标文件可写（``app.log`` 本身可能被别的
    进程占着、或被安全软件锁死），所以这里还要再兜一层。
    """
    for path in (LOG_PATH, DATA_DIR / "app.log"):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
        except Exception:
            continue
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        return handler
    return None


def _has_file_handler(root: logging.Logger, handler: RotatingFileHandler) -> bool:
    """根日志上是否已经有指向同一文件的 handler（避免重复导入叠加）。"""
    target = Path(getattr(handler, "baseFilename", ""))
    return any(
        isinstance(existing, RotatingFileHandler)
        and Path(getattr(existing, "baseFilename", "")) == target
        for existing in root.handlers
    )


def configure_logging() -> None:
    """幂等配置根日志，重复导入模块不会叠加 handler。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        file_handler = _build_file_handler(formatter)
        if file_handler is not None and not _has_file_handler(root, file_handler):
            root.addHandler(file_handler)
        _CONFIGURED = True
