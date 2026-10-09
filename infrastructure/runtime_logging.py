"""应用日志配置：错误日志落盘并按大小轮转。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

from infrastructure.paths import LOG_DIR


_CONFIG_LOCK = Lock()
_CONFIGURED = False
# 日志写到项目之外的用户级目录（见 infrastructure/paths.py 的 LOG_DIR 说明）
LOG_PATH = LOG_DIR / "app.log"
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
        log_path = LOG_PATH
        file_handler = None
        candidates = (
            LOG_PATH,
            LOG_PATH.with_name(f"{LOG_PATH.stem}-{os.getpid()}{LOG_PATH.suffix}"),
            Path(__file__).resolve().parents[1] / "logs" / f"app-{os.getpid()}.log",
        )
        for candidate in candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    candidate,
                    maxBytes=MAX_BYTES,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
                log_path = candidate
                if candidate != LOG_PATH:
                    print(
                        f"[logging] {LOG_PATH} 不可用，当前进程改写入 {candidate}",
                        flush=True,
                    )
                break
            except PermissionError:
                continue
        if file_handler is None:
            # 最后兜底仍保留日志，不让日志目录权限阻断应用导入或测试收集。
            file_handler = logging.StreamHandler()
            log_path = Path("")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(
            isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")) == log_path
            for handler in root.handlers
        ):
            root.addHandler(file_handler)
        _CONFIGURED = True
