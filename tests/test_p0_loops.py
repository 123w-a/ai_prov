"""P0 双闭环回归：拍照清点（视觉解析/端点）+ 用餐反馈（纯函数/端点/周报集成）。"""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main_app import app
from api.routes import fridge_route, reports_route
from benchmarks.historical_cases import CASES  # noqa: F401  确保规则引擎同仓可用


class VisionParseTest(unittest.TestCase):
    def test_plain_json_list(self):
        items = fridge_route._parse_vision_json('[{"name":"鸡蛋","quantity":"3个"}]')

        self.assertEqual(items, [{"name": "鸡蛋", "quantity": "3个"}])

    def test_fenced_json_is_stripped(self):
        raw = '\u0060\u0060\u0060json\n[{"name":"西兰花","quantity":""}]\n\u0060\u0060\u0060'

        items = fridge_route._parse_vision_json(raw)

        self.assertEqual(items, [{"name": "西兰花", "quantity": ""}])

    def test_garbage_returns_none_and_entries_without_name_dropped(self):
        self.assertIsNone(fridge_route._parse_vision_json("我觉得图里有鸡蛋"))
        self.assertEqual(fridge_route._parse_vision_json('[{"quantity":"2"}]'), [])


class VisionEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_vision_endpoint_returns_draft(self):
        with patch.object(fridge_route, "_vision_extract_items", return_value=[{"name": "鸡蛋", "quantity": "3个"}]):
            resp = self.client.post(
                "/api/fridge/vision",
                files={"image": ("fridge.jpg", b"fake-bytes", "image/jpeg")},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["draft"])
        self.assertEqual(payload["items"][0]["name"], "鸡蛋")

    def test_vision_endpoint_maps_failure_to_502(self):
        with patch.object(fridge_route, "_vision_extract_items", side_effect=ValueError("解析失败")):
            resp = self.client.post(
                "/api/fridge/vision",
                files={"image": ("fridge.jpg", b"fake-bytes", "image/jpeg")},
            )

        self.assertEqual(resp.status_code, 502)
        self.assertIn("视觉识别失败", resp.json()["detail"])


class FeedbackTipsTest(unittest.TestCase):
    def test_no_entries_no_tips(self):
        self.assertEqual(reports_route._feedback_tips([], today=datetime(2026, 8, 28)), [])

    def test_repeated_salty_tag_gets_tip(self):
        entries = [
            {"ts": "2026-08-26T12:00:00", "dish": "红烧肉", "rating": 3, "tags": ["偏咸"]},
            {"ts": "2026-08-27T12:00:00", "dish": "咸鱼", "rating": 2, "tags": ["偏咸"]},
        ]

        tips = reports_route._feedback_tips(entries, today=datetime(2026, 8, 28))

        self.assertEqual(len(tips), 1)
        self.assertIn("偏咸", tips[0])
        self.assertIn("2 餐", tips[0])

    def test_low_average_rating_suggests_change(self):
        entries = [
            {"ts": "2026-08-26T12:00:00", "dish": "A", "rating": 1, "tags": []},
            {"ts": "2026-08-27T12:00:00", "dish": "B", "rating": 2, "tags": []},
        ]

        tips = reports_route._feedback_tips(entries, today=datetime(2026, 8, 28))

        self.assertEqual(len(tips), 1)
        self.assertIn("1.5 分", tips[0])

    def test_old_feedback_out_of_window_ignored(self):
        entries = [{"ts": "2026-07-01T12:00:00", "dish": "A", "rating": 1, "tags": ["偏咸"]}]

        self.assertEqual(reports_route._feedback_tips(entries, today=datetime(2026, 8, 28)), [])


class FeedbackEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.path = Path(__file__).with_name(".feedback-test.json")

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_save_and_invalid_rating(self):
        with patch.object(reports_route, "_FEEDBACK_LOG", self.path):
            ok = self.client.post("/api/reports/feedback", json={
                "dish": "番茄炒蛋", "rating": 4, "tags": ["好吃"], "comment": "",
            })
            bad = self.client.post("/api/reports/feedback", json={"dish": "X", "rating": 9})

        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["saved"])
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 1)

    def test_weekly_report_includes_feedback_summary(self):
        with patch.object(reports_route, "_FEEDBACK_LOG", self.path):
            self.client.post("/api/reports/feedback", json={"dish": "清蒸鱼", "rating": 5, "tags": ["好吃"]})
            resp = self.client.get("/api/reports/weekly")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["feedback_summary"]["count"], 1)
        self.assertIn("好吃", payload["feedback_summary"]["tags"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
