"""应用日志配置：错误日志落盘并按大小轮转。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock


_CONFIG_LOCK = Lock()
_CONFIGURED = False
LOG_PATH = Path(__file__).resolve().parent / "data" / "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5


def configure_logging() -> None:
    """幂等配置根日志，重复导入模块不会叠加 handler。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        file_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(
            isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")) == LOG_PATH
            for handler in root.handlers
        ):
            root.addHandler(file_handler)
        _CONFIGURED = True
