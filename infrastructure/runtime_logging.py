"""应用日志配置：错误日志落盘并按大小轮转。"""

from __future__ import annotations

import logging
import os
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
    """建文件 handler；首选位置写不进去就逐级回退，都不行就当没有。

    2026-10-14：只落盘、不留兜底，会让"写日志"把主链路带崩——本模块自己的约定
    是**日志失败一律静默**（见 ``paths.LOG_DIR`` 的文档）。``paths`` 那边现在会
    真写一次来判可写，但目录可写 ≠ 目标文件可写（``app.log`` 本身可能被别的
    进程占着、或被安全软件锁死），所以这里还要再兜一层。
    """
    candidates = (
        LOG_PATH,
        LOG_PATH.with_name(f"{LOG_PATH.stem}-{os.getpid()}{LOG_PATH.suffix}"),
        DATA_DIR / "app.log",
        Path(__file__).resolve().parents[1] / "logs" / f"app-{os.getpid()}.log",
    )
    for path in dict.fromkeys(candidates):
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
        if path != LOG_PATH:
            print(
                f"[logging] {LOG_PATH} 不可用，当前进程改写入 {path}",
                flush=True,
            )
        handler.setFormatter(formatter)
        handler.setLevel(logging.INFO)
        return handler
    return None


def _has_file_handler(root: logging.Logger, handler: logging.Handler) -> bool:
    """根日志上是否已经有指向同一文件的 handler（避免重复导入叠加）。"""
    filename = getattr(handler, "baseFilename", None)
    if filename is None:
        return False
    target = Path(filename)
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
        if file_handler is None:
            # 最后兜底仍保留日志，不让日志目录权限阻断应用导入或测试收集。
            file_handler = logging.StreamHandler()
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.INFO)
        if file_handler is not None and not _has_file_handler(root, file_handler):
            root.addHandler(file_handler)
        _CONFIGURED = True
