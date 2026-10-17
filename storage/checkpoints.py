"""checkpoint.db 生命周期管理：删会话时清断点、清理孤儿线程、回收文件空间。

问题背景
--------
Agent 的多轮断点存在 ``resources/checkpoint.db``（见 ``agent/graph.py`` 的
``SqliteSaver``），但会话的增删只动 ``sessions/*.json``：删掉一个会话后，
它在断点库里的整条 thread（全部 checkpoints / writes）仍然留着。
长期只增不减——实测 117MB、245 个 thread，而磁盘上只剩 3 个会话文件。

本模块是断点清理的唯一入口，三条对外能力：

- ``delete_session_threads(sid)``：删某个会话的全部断点线程（含 failover
  派生的 ``<sid>-fo<时间戳>`` 线程），供 ``storage.sessions.delete_session`` 调用；
- ``purge_orphans(active_ids)``：删掉磁盘上已经没有对应会话文件的孤儿线程；
- ``vacuum()``：VACUUM 回收文件空间。

清理一律「尽力而为」：任何失败只记日志、不抛给调用方，绝不让清理动作
影响会话本身的读写。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from langgraph.checkpoint.sqlite import SqliteSaver

from infrastructure.paths import CHECKPOINT_DB

_logger = logging.getLogger("checkpoint_store")

# 单个 SQLite 连接的路径（测试可 patch 后自动重连，见 _get_saver）
DB_PATH: Path = CHECKPOINT_DB

# failover 整轮重跑用的派生线程：<session_id>-fo<unix 秒>（chat_route.run_agent）。
# 它们和主线程同属一个会话，归属判断时要把这个后缀剥掉。
_FO_SUFFIX = re.compile(r"-fo[0-9]+$")

# 串行化本模块自己的连接操作；跨进程/跨连接的并发由 SQLite 的 busy timeout 兜。
_LOCK = threading.Lock()
_saver: SqliteSaver | None = None
_saver_path: str | None = None


def base_session_id(thread_id: str) -> str:
    """把派生线程 ``<sid>-fo<时间戳>`` 归回它所属的会话 id。"""
    return _FO_SUFFIX.sub("", str(thread_id))


def _get_saver() -> SqliteSaver | None:
    """惰性打开断点库。路径被改（测试）或首次使用时重建连接。"""
    global _saver, _saver_path
    target = str(DB_PATH)
    with _LOCK:
        if _saver is not None and _saver_path == target:
            return _saver
        if _saver is not None:  # 路径变了：关掉旧连接再重开
            try:
                _saver.conn.close()
            except Exception:
                pass
            _saver = None
            _saver_path = None
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(target, check_same_thread=False, timeout=30)
            saver = SqliteSaver(conn)
            saver.setup()  # 建表（幂等）+ WAL，保证空库也能安全查询
        except Exception as exc:
            _logger.warning("断点库不可用，跳过断点清理：%s", exc)
            return None
        _saver = saver
        _saver_path = target
        return _saver


def all_thread_ids() -> list[str]:
    """断点库里出现过的全部 thread_id（含只在 writes 里的）。"""
    saver = _get_saver()
    if saver is None:
        return []
    try:
        with saver.cursor(transaction=False) as cur:
            cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints "
                "UNION SELECT DISTINCT thread_id FROM writes"
            )
            return [str(row[0]) for row in cur.fetchall() if row[0]]
    except Exception as exc:
        _logger.warning("读取断点线程列表失败：%s", exc)
        return []


def delete_threads(thread_ids: Iterable[str]) -> int:
    """删除给定线程的全部断点与 writes，返回成功删除的线程数。"""
    targets = sorted({str(t) for t in thread_ids if str(t or "").strip()})
    if not targets:
        return 0
    saver = _get_saver()
    if saver is None:
        return 0
    deleted = 0
    for thread_id in targets:
        try:
            saver.delete_thread(thread_id)
            deleted += 1
        except Exception as exc:
            _logger.warning("删除断点线程失败 thread=%s：%s", thread_id, exc)
    return deleted


def delete_session_threads(sid: str) -> int:
    """删除某个会话的全部断点线程（含 ``-fo`` 派生线程）。"""
    sid = str(sid or "").strip()
    if not sid:
        return 0
    return delete_threads(t for t in all_thread_ids() if base_session_id(t) == sid)


def purge_orphans(active_session_ids: Iterable[str]) -> dict:
    """删除磁盘上已无对应会话文件的孤儿线程。

    ``active_session_ids`` 是当前 ``sessions/*.json`` 的文件名（不带扩展名）。
    返回统计：库内线程总数、孤儿数、实际删除数。
    """
    active = {str(s).strip() for s in active_session_ids if str(s or "").strip()}
    threads = all_thread_ids()
    orphans = [t for t in threads if base_session_id(t) not in active]
    return {
        "threads": len(threads),
        "orphans": len(orphans),
        "deleted": delete_threads(orphans),
    }


def vacuum() -> bool:
    """VACUUM 回收删除后的文件空间。失败只记日志。"""
    saver = _get_saver()
    if saver is None:
        return False
    try:
        with _LOCK:
            saver.conn.commit()
            saver.conn.execute("VACUUM")
            saver.conn.commit()
        return True
    except Exception as exc:
        _logger.warning("断点库 VACUUM 失败（不影响使用）：%s", exc)
        return False


def close() -> None:
    """关闭本模块持有的连接（脚本收尾/测试清理用）。"""
    global _saver, _saver_path
    with _LOCK:
        if _saver is not None:
            try:
                _saver.conn.close()
            except Exception:
                pass
        _saver = None
        _saver_path = None
