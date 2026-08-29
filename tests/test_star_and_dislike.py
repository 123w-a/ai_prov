"""收藏功能 + 忌口沉淀端点：toggle 语义 / 去重 / 聚合扫描。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sessions_store
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
