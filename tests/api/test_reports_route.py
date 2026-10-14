"""Weekly report trend aggregation regression tests."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import preferences_route as pr
from api.routes import reports_route as rr
from api.routes.reports_route import (
    _light_trends,
    _pantry_restock_tips,
    _weekly_recommendations,
    member_nutrition_progress,
    week_window,
)


def meal(ts, lights):
    return {"ts": ts.isoformat(timespec="seconds"), "lights": lights}


class WeeklyRecommendationTest(unittest.TestCase):
    def test_worsening_trend_maps_to_label_specific_tip(self):
        tips = _weekly_recommendations([], {"钠": "worsening", "糖": "stable"})

        self.assertEqual(len(tips), 1)
        self.assertIn("钠", tips[0])

    def test_insufficient_data_gets_no_directional_advice(self):
        self.assertEqual(_weekly_recommendations([("番茄炒蛋", 3)], {"钠": "insufficient"}), [])
        self.assertEqual(_weekly_recommendations([], {}), [])

    def test_healthy_stable_trends_suggest_variety(self):
        tips = _weekly_recommendations([("番茄炒蛋", 2), ("青椒肉丝", 1)], {"钠": "stable", "糖": "improving"})

        self.assertEqual(len(tips), 1)
        self.assertIn("番茄炒蛋、青椒肉丝", tips[0])


class LightTrendTest(unittest.TestCase):
    def test_compares_risk_ratios_between_windows(self):
        since = datetime(2026, 8, 20, 0, 0, 0)
        recent = [
            meal(since + timedelta(days=1), ["钠:yellow", "糖:green", "脂肪:green"]),
            meal(since + timedelta(days=2), ["钠:red", "糖:green", "脂肪:green"]),
            meal(since + timedelta(days=4, hours=1), ["钠:green", "糖:yellow", "脂肪:green"]),
            meal(since + timedelta(days=5), ["钠:green", "糖:red", "脂肪:green"]),
        ]

        self.assertEqual(_light_trends(recent, since), {"钠": "improving", "糖": "worsening", "脂肪": "stable"})

    def test_marks_sparse_history_as_insufficient(self):
        since = datetime(2026, 8, 20, 0, 0, 0)
        recent = [
            meal(since + timedelta(days=1), ["钠:yellow"]),
            meal(since + timedelta(days=5), ["钠:green"]),
        ]

        self.assertEqual(_light_trends(recent, since), {"钠": "insufficient"})




class PantryRestockTest(unittest.TestCase):
    def test_repeated_purchases_missing_from_fridge_get_tip(self):
        log = [{"date": "2026-08-20", "type": "purchase", "item": "鸡蛋"},
               {"date": "2026-08-25", "type": "purchase", "item": "鸡蛋"}]

        tips = _pantry_restock_tips(log, set(), today=datetime(2026, 8, 27))

        self.assertEqual(len(tips), 1)
        self.assertIn("鸡蛋", tips[0])
        self.assertIn("2 次", tips[0])

    def test_items_currently_owned_are_suppressed(self):
        log = [{"date": "2026-08-20", "type": "purchase", "item": "牛奶"},
               {"date": "2026-08-26", "type": "purchase", "item": "牛奶"}]

        self.assertEqual(_pantry_restock_tips(log, {"牛奶"}, today=datetime(2026, 8, 27)), [])

    def test_old_events_and_single_purchase_are_ignored(self):
        log = [{"date": "2026-08-01", "type": "purchase", "item": "酸奶"},
               {"date": "2026-08-10", "type": "purchase", "item": "酸奶"},
               {"date": "2026-08-26", "type": "purchase", "item": "豆腐"}]

        self.assertEqual(_pantry_restock_tips(log, set(), today=datetime(2026, 8, 27)), [])

    def test_top_two_by_frequency(self):
        log = ([{"date": "2026-08-20", "type": "purchase", "item": a} for a in ["豆浆"] * 3]
               + [{"date": "2026-08-21", "type": "purchase", "item": b} for b in ["香蕉"] * 2]
               + [{"date": "2026-08-22", "type": "purchase", "item": c} for c in ["燕麦"] * 2])

        tips = _pantry_restock_tips(log, {"燕麦"}, today=datetime(2026, 8, 27))

        self.assertEqual(len(tips), 2)
        self.assertIn("豆浆", tips[0])
        self.assertIn("香蕉", tips[1])


class WeekWindowTest(unittest.TestCase):
    """成员营养进度的周期必须与周报的 range 同源——"这一周"只能有一个口径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.meals_path = self.data_dir / "meals.json"
        self.meals_path.write_text(json.dumps([
            {"ts": datetime.now().isoformat(timespec="seconds"),
             "session": "s1", "dish": "番茄炒蛋",
             "lights": ["钠:green", "糖:green", "脂肪:green"], "guardrails": 0},
        ], ensure_ascii=False), encoding="utf-8")
        for p in (
            patch.object(rr, "_MEALS", self.meals_path),
            patch.object(rr, "get_fridge", lambda: {"items": []}),
            patch.object(rr, "_read_pantry_log", lambda: []),
            patch.object(rr, "_read_feedback_log", lambda: []),
            patch.object(pr, "_PROFILE_PATH", self.data_dir / "profile.json"),
            patch.object(pr, "_DATA_DIR", self.data_dir),
        ):
            p.start()
            self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(rr.router)
        app.include_router(pr.router)
        self.client = TestClient(app)

    def test_member_progress_period_matches_weekly_range(self):
        weekly = self.client.get("/reports/weekly").json()
        self.assertTrue(weekly["has_data"])
        fam = self.client.get("/profile").json()["data"]["family"]
        period = fam["members"][0]["profile"]["nutrition_progress"]["period"]
        self.assertEqual([period["start"], period["end"]], weekly["range"])

    def test_window_is_seven_days_ending_today(self):
        start, now = week_window(datetime(2026, 10, 11, 20, 0, 0))
        self.assertEqual(start, datetime(2026, 10, 4, 20, 0, 0))
        self.assertEqual(now, datetime(2026, 10, 11, 20, 0, 0))

    def test_progress_zero_denominator_is_null_and_shape_is_fixed(self):
        """口径已定（三盏灯全绿）；这份 fixture 的餐次**未归属到任何人**。

        所以任一成员的分母都是 0 → value null（不是 0%），且字段形状固定为四个键。
        判据本身的边界在 tests/api/test_nutrition_progress.py。
        """
        progress = member_nutrition_progress({"name": "小美"})
        self.assertEqual(set(progress), {"value", "numerator", "denominator", "period"})
        self.assertIsNone(progress["value"])
        self.assertEqual(progress["numerator"], 0)
        self.assertEqual(progress["denominator"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
