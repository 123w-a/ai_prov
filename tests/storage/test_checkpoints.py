"""断点库生命周期回归：删会话清断点、孤儿线程清理、VACUUM。

覆盖 ``storage/checkpoints.py`` 的三条对外能力。这里直接向 ``SqliteSaver``
建好的两张表里塞行构造夹具——比走 ``put``/``put_writes`` 更贴近我们要
验证的「按 thread_id 增删」语义，也不依赖 Checkpoint 的完整结构。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from storage import checkpoints
from storage import sessions as sessions_store


def _seed_threads(saver, thread_ids):
    """给每个 thread_id 在 checkpoints / writes 两表各插一行。"""
    for idx, thread_id in enumerate(thread_ids):
        saver.conn.execute(
            "INSERT INTO checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata) "
            "VALUES (?, '', ?, 'json', ?, ?)",
            (thread_id, f"cp{idx}", b"x", b"y"),
        )
        saver.conn.execute(
            "INSERT INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "VALUES (?, '', ?, 'task', 0, 'ch', 'json', ?)",
            (thread_id, f"cp{idx}", b"z"),
        )
    saver.conn.commit()


def _count_rows(conn, table, thread_id):
    cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", (thread_id,))
    return cur.fetchone()[0]


class CheckpointStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "checkpoint.db"
        self._orig_db_path = checkpoints.DB_PATH
        checkpoints.DB_PATH = self.db_path
        # LIFO：先关连接放掉文件句柄，再让 TemporaryDirectory 清理临时目录。
        self.addCleanup(self._restore_module)

    def _restore_module(self):
        checkpoints.close()
        checkpoints.DB_PATH = self._orig_db_path

    def _saver(self):
        saver = checkpoints._get_saver()
        self.assertIsNotNone(saver)
        return saver

    # —— base_session_id ——

    def test_base_session_id_strips_failover_suffix(self):
        self.assertEqual(checkpoints.base_session_id("user_abc"), "user_abc")
        self.assertEqual(checkpoints.base_session_id("user_abc-fo1697000000"), "user_abc")
        # 只在结尾出现才剥；中间出现属于会话名本身
        self.assertEqual(checkpoints.base_session_id("user-fo1abc"), "user-fo1abc")
        # 整串就是派生后缀时剥成空串（无害：它永远匹配不上任何真实会话文件名）
        self.assertEqual(checkpoints.base_session_id("-fo12"), "")

    # —— all_thread_ids ——

    def test_all_thread_ids_unions_both_tables(self):
        saver = self._saver()
        _seed_threads(saver, ["s1", "s2"])
        # 只在 writes 里出现的线程也要被认到
        saver.conn.execute(
            "INSERT INTO writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
            "VALUES ('writes-only', '', 'cp', 'task', 0, 'ch', 'json', ?)",
            (b"z",),
        )
        saver.conn.commit()

        ids = checkpoints.all_thread_ids()
        self.assertEqual(set(ids), {"s1", "s2", "writes-only"})

    def test_all_thread_ids_empty_when_db_missing(self):
        self.assertEqual(checkpoints.all_thread_ids(), [])

    # —— delete_threads / delete_session_threads ——

    def test_delete_threads_removes_checkpoint_and_writes(self):
        saver = self._saver()
        _seed_threads(saver, ["s1", "s2"])

        self.assertEqual(checkpoints.delete_threads(["s1"]), 1)
        self.assertEqual(_count_rows(saver.conn, "checkpoints", "s1"), 0)
        self.assertEqual(_count_rows(saver.conn, "writes", "s1"), 0)
        # 其它线程原样保留
        self.assertEqual(_count_rows(saver.conn, "checkpoints", "s2"), 1)
        self.assertEqual(_count_rows(saver.conn, "writes", "s2"), 1)
        self.assertEqual(checkpoints.all_thread_ids(), ["s2"])

    def test_delete_session_threads_removes_failover_variants(self):
        saver = self._saver()
        _seed_threads(saver, ["s1", "s1-fo1697000000", "s1-fo1697000001", "s2"])

        # 主线程 + 两个 failover 派生线程一起清掉，别的会话不动
        self.assertEqual(checkpoints.delete_session_threads("s1"), 3)
        self.assertEqual(checkpoints.all_thread_ids(), ["s2"])

    def test_delete_session_threads_blank_sid_is_noop(self):
        saver = self._saver()
        _seed_threads(saver, ["s1"])
        self.assertEqual(checkpoints.delete_session_threads("   "), 0)
        self.assertEqual(checkpoints.all_thread_ids(), ["s1"])

    # —— purge_orphans ——

    def test_purge_orphans_keeps_active_deletes_rest(self):
        saver = self._saver()
        _seed_threads(
            saver,
            ["keep", "keep-fo1", "gone1", "gone2", "gone2-fo1"],
        )

        stats = checkpoints.purge_orphans({"keep"})
        self.assertEqual(stats["threads"], 5)
        self.assertEqual(stats["orphans"], 3)
        self.assertEqual(stats["deleted"], 3)
        # 活跃会话（含 failover 派生）保留
        self.assertEqual(set(checkpoints.all_thread_ids()), {"keep", "keep-fo1"})

    def test_purge_orphans_nothing_to_do(self):
        saver = self._saver()
        _seed_threads(saver, ["keep"])
        stats = checkpoints.purge_orphans({"keep"})
        self.assertEqual(stats, {"threads": 1, "orphans": 0, "deleted": 0})

    # —— vacuum ——

    def test_vacuum_succeeds_and_reclaims_after_delete(self):
        saver = self._saver()
        _seed_threads(saver, ["a", "b", "c"])
        checkpoints.delete_threads(["a", "b", "c"])
        self.assertTrue(checkpoints.vacuum())
        self.assertEqual(checkpoints.all_thread_ids(), [])

    # —— 尽力而为：库不可用时一律不抛 ——

    def test_all_operations_degrade_when_db_unavailable(self):
        with patch.object(checkpoints, "_get_saver", return_value=None):
            self.assertEqual(checkpoints.all_thread_ids(), [])
            self.assertEqual(checkpoints.delete_threads(["s1"]), 0)
            self.assertEqual(checkpoints.delete_session_threads("s1"), 0)
            self.assertEqual(checkpoints.purge_orphans({"s1"}), {"threads": 0, "orphans": 0, "deleted": 0})
            self.assertFalse(checkpoints.vacuum())


class DeleteSessionPurgeTest(unittest.TestCase):
    """删会话时联动清断点：这是 P1 的入口，断点清理不能反过来影响会话删除。"""

    def test_delete_session_purges_checkpoint_threads(self):
        data = sessions_store.create_session()
        sid = data["session_id"]
        self.assertTrue((Path(sessions_store.SESSIONS_DIR) / f"{sid}.json").exists())

        with patch.object(checkpoints, "delete_session_threads", Mock(return_value=2)) as mocked:
            sessions_store.delete_session(sid)

        mocked.assert_called_once_with(sid)
        self.assertFalse((Path(sessions_store.SESSIONS_DIR) / f"{sid}.json").exists())

    def test_delete_session_still_succeeds_when_purge_raises(self):
        data = sessions_store.create_session()
        sid = data["session_id"]

        with patch.object(
            checkpoints,
            "delete_session_threads",
            Mock(side_effect=RuntimeError("db locked")),
        ):
            # 清理失败只记 warning，会话本身必须照删
            sessions_store.delete_session(sid)

        self.assertFalse((Path(sessions_store.SESSIONS_DIR) / f"{sid}.json").exists())


if __name__ == "__main__":
    unittest.main()
