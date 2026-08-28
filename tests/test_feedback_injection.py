"""反馈驱动推荐：近踩菜名统计 + build_human_message 注入约束。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feedback_store
from feedback_store import recent_down_dishes
from main import build_human_message


def _event(ts, rating, dish):
    return {"ts": ts, "sid": "s", "rec_id": 1, "rating": rating, "dish": dish}


class FeedbackStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(
            feedback_store, "_LOG", Path(self.tmp.name) / "answer_feedback.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recent_down_dishes_window_and_rank(self):
        feedback_store.write_events([
            _event("2026-08-28T10:00:00", "down", "酸辣土豆丝"),
            _event("2026-08-28T11:00:00", "down", "酸辣土豆丝"),
            _event("2026-08-28T12:00:00", "down", "糖醋排骨"),
            _event("2026-08-28T13:00:00", "up", "红烧带鱼"),
            _event("2026-01-01T00:00:00", "down", "陈年旧菜"),
        ])
        self.assertEqual(recent_down_dishes(days=7), ["酸辣土豆丝", "糖醋排骨"])
        self.assertEqual(recent_down_dishes(limit=1), ["酸辣土豆丝"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(recent_down_dishes(), [])


class InjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(
            feedback_store, "_LOG", Path(self.tmp.name) / "answer_feedback.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        prefs_patcher = patch("main.load_preferences", return_value="忌口：香菜")
        prefs_patcher.start()
        self.addCleanup(prefs_patcher.stop)

    def test_down_dishes_injected_into_message(self):
        feedback_store.write_events([
            _event("2026-08-28T10:00:00", "down", "糖醋排骨"),
        ])
        msg = build_human_message("想吃点开胃的")
        content = msg.content
        self.assertIn("近期不满意菜品", content)
        self.assertIn("糖醋排骨", content)
        self.assertIn("忌口：香菜", content)
        self.assertIn("想吃点开胃的", content)

    def test_no_events_no_injection(self):
        msg = build_human_message("今晚吃什么")
        self.assertNotIn("近期不满意菜品", msg.content)
        self.assertIn("【用户长期偏好", msg.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
