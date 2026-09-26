"""本地运行数据的可靠写入工具。

当前项目刻意使用单进程 JSON 存储。这里只解决两个稳定性问题：
1. 写入过程中进程退出或磁盘抖动时，不留下半截 JSON；
2. 覆盖前保留最近一份 ``.bak``，便于人工恢复。

不引入数据库或跨进程锁，保持现有部署方式不变。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    backup: bool = True,
) -> None:
    """原子写入文本：临时文件同目录落盘后 ``os.replace`` 覆盖目标。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

        if backup and target.exists():
            backup_path = _backup_path(target)
            backup_tmp = backup_path.with_name(
                f".{backup_path.name}.{os.getpid()}.tmp"
            )
            try:
                shutil.copy2(target, backup_tmp)
                os.replace(backup_tmp, backup_path)
            finally:
                try:
                    backup_tmp.unlink(missing_ok=True)
                except Exception:
                    pass

        os.replace(tmp_path, target)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    backup: bool = True,
) -> None:
    """把可 JSON 序列化对象原子写入目标文件。"""
    text = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(path, text, backup=backup)
