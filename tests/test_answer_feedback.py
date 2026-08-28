"""回答满意度反馈闭环：状态 patch（toggle 语义）+ 周统计事件文件。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.session_route as sr
from sessions_store import _read_session, _write_session, patch_message_feedback


def _client():
    app = FastAPI()
    app.include_router(sr.router)
    return TestClient(app)


class AnswerFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = patch.object(
            sr.feedback_store, "_LOG", self.dir / "answer_feedback.json"
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.events_file = self.dir / "answer_feedback.json"
        # 造一个已存在会话（绕过磁盘定位：直接 patch _read_session 返回内存数据）
        self.session = {
            "session_id": "fb_s1", "title": "t", "created_at": "12:00",
            "messages": [
                {"id": 1, "user_text": "吃什么", "answer": json.dumps({
                    "recipes": [{"name": "番茄炖牛腩", "image_url": None}],
                }, ensure_ascii=False)},
            ],
        }
        patcher_r = patch("sessions_store._read_session", lambda sid: self.session)
        patcher_sr = patch.object(sr, "_read_session", lambda sid: self.session)
        patcher_w = patch("sessions_store._write_session", lambda data: None)
        for p in (patcher_r, patcher_sr, patcher_w):
            p.start()
            self.addCleanup(p.stop)
        self.client = _client()

    def test_patch_feedback_toggle_states(self):
        found, cur = patch_message_feedback("fb_s1", 1, "up")
        self.assertTrue(found)
        self.assertEqual(cur, "up")
        found, cur = patch_message_feedback("fb_s1", 1, "down")
        self.assertEqual(cur, "down")
        found, cur = patch_message_feedback("fb_s1", 1, None)
        self.assertTrue(found)
        self.assertIsNone(cur)
        found, _ = patch_message_feedback("fb_s1", 999, "up")
        self.assertFalse(found)

    def test_feedback_endpoint_writes_event_and_toggles(self):
        r1 = self.client.post(
            "/sessions/fb_s1/messages/1/feedback", json={"rating": "up"})
        self.assertEqual(r1.json()["data"]["feedback"], "up")
        events = json.loads(self.events_file.read_text(encoding="utf-8"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["rating"], "up")
        self.assertEqual(events[0]["dish"], "番茄炖牛腩")
        # 同值再点 = 取消 → 事件移除
        r2 = self.client.post(
            "/sessions/fb_s1/messages/1/feedback", json={"rating": "up"})
        self.assertIsNone(r2.json()["data"]["feedback"])
        self.assertEqual(self.events_file.read_text(encoding="utf-8").strip(), "[]")

    def test_weekly_summary_counts(self):
        self.events_file.write_text(json.dumps([
            {"ts": "2026-08-28T10:00:00", "sid": "a", "rec_id": 1,
             "rating": "up", "dish": None},
            {"ts": "2026-08-28T11:00:00", "sid": "b", "rec_id": 1,
             "rating": "down", "dish": "蒜香西兰花鸡蛋面"},
            {"ts": "2026-08-28T12:00:00", "sid": "c", "rec_id": 1,
             "rating": "down", "dish": "蒜香西兰花鸡蛋面"},
            {"ts": "2026-01-01T00:00:00", "sid": "old", "rec_id": 1,
             "rating": "down", "dish": "陈年旧菜"},
        ], ensure_ascii=False), encoding="utf-8")
        data = self.client.get("/feedback/weekly").json()["data"]
        self.assertEqual(data["up"], 1)
        self.assertEqual(data["down"], 2)
        self.assertEqual(data["down_dishes"], ["蒜香西兰花鸡蛋面×2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
