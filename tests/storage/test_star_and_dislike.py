"""收藏功能 + 忌口沉淀端点：toggle 语义 / 去重 / 聚合扫描 / 收藏时间戳。"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from storage import sessions as sessions_store
import api.routes.preferences_route as pr
import api.routes.session_route as sr


class StarStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session = {
            "session_id": "s_star", "title": "t", "created_at": "12:00",
            "messages": [{"id": 1, "user_text": "q", "answer": "a"}],
        }

    def _list_starred(self):
        """把 self.session 落成一个真文件再走 list_starred 的磁盘扫描。"""
        tmp_dir = Path(self.tmp.name)
        (tmp_dir / "s_star.json").write_text(
            json.dumps(self.session, ensure_ascii=False), encoding="utf-8"
        )
        with patch.object(sessions_store, "SESSIONS_DIR", tmp_dir):
            return sessions_store.list_starred()

    def test_star_toggle_and_scan(self):
        with patch.object(sessions_store, "_read_session", lambda sid: self.session), patch.object(
            sessions_store, "_write_session", lambda data: None
        ):
            found, cur = sessions_store.star_message("s_star", 1, True)
            self.assertTrue(found and cur)
            self.assertTrue(self.session["messages"][0]["starred"])
            found, cur = sessions_store.star_message("s_star", 1, True)  # 幂等
            self.assertTrue(cur)
            found, cur = sessions_store.star_message("s_star", 1, False)  # 取消
            self.assertTrue(found and not cur)
            self.assertNotIn("starred", self.session["messages"][0])

    def test_star_records_favorited_at_and_clears_it_on_unstar(self):
        """收藏写 starred_at（≈ 点下去那一刻，格式与 time 同款）；取消收藏一并清掉。

        同时钉住：重复点收藏**不刷新**时间戳（重复点击不是新的收藏动作）。
        """
        self.session["messages"][0]["time"] = "2026-08-26 13:35:50"
        with patch.object(sessions_store, "_read_session", lambda sid: self.session), patch.object(
            sessions_store, "_write_session", lambda data: None
        ):
            before = datetime.now()
            sessions_store.star_message("s_star", 1, True)
            m = self.session["messages"][0]
            self.assertIn("starred_at", m)
            stamped = datetime.strptime(m["starred_at"], "%Y-%m-%d %H:%M:%S")
            self.assertLessEqual(abs((stamped - before).total_seconds()), 120)
            # 这一顿提问的时间原样不动（两个时间不是一回事）
            self.assertEqual(m["time"], "2026-08-26 13:35:50")

            first = m["starred_at"]
            sessions_store.star_message("s_star", 1, True)
            self.assertEqual(m["starred_at"], first)

            sessions_store.star_message("s_star", 1, False)
            self.assertNotIn("starred", m)
            self.assertNotIn("starred_at", m)

    def test_list_starred_exposes_favorited_at_without_touching_turned_at(self):
        m = self.session["messages"][0]
        m["starred"] = True
        m["time"] = "2026-08-26 13:35:50"
        m["starred_at"] = "2026-10-11 19:20:07"
        items = self._list_starred()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["favorited_at"], "2026-10-11 19:20:07")
        self.assertEqual(items[0]["turned_at"], "2026-08-26 13:35:50")

    def test_list_starred_favorited_at_null_for_old_records(self):
        """旧收藏（star 还不落时间的那批）→ favorited_at 为 null。

        不许拿 turned_at、也不许拿别的旁证补一个像样的时刻。
        """
        m = self.session["messages"][0]
        m["starred"] = True
        m["time"] = "2026-08-26 13:35:50"
        items = self._list_starred()
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["favorited_at"])
        self.assertEqual(items[0]["turned_at"], "2026-08-26 13:35:50")

    def test_list_starred_handles_plain_text_answer(self):
        self.session["messages"][0]["starred"] = True
        self.session["messages"][0]["answer"] = "纯文本回答"
        self.session["messages"][0]["image_url"] = None
        tmp_dir = Path(self.tmp.name)
        (tmp_dir / "s_star.json").write_text(json.dumps(self.session, ensure_ascii=False), encoding="utf-8")
        with patch.object(sessions_store, "SESSIONS_DIR", tmp_dir):
            items = sessions_store.list_starred()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["dish"], "q")
        self.assertIsNone(items[0]["answer"])


class DislikeAddTest(unittest.TestCase):
    def test_add_dedup_and_active_default(self):
        family = {
            "active_id": "m_a",
            "members": [
                {"id": "m_a", "name": "小美", "profile": {"dislikes": ["芹菜"]}},
            ],
        }
        with patch.object(pr, "_read_family", lambda: family), patch.object(
            pr, "_write_family", lambda data: None
        ):
            r1 = pr.add_dislike(pr.DislikeAddPayload(item="香菜"))   # 缺省=活跃成员
            self.assertTrue(r1["data"]["added"])
            self.assertEqual(family["members"][0]["profile"]["dislikes"], ["芹菜", "香菜"])
            r2 = pr.add_dislike(pr.DislikeAddPayload(item="香菜"))   # 重复 → 不重复追加
            self.assertFalse(r2["data"]["added"])
            self.assertEqual(len(family["members"][0]["profile"]["dislikes"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
