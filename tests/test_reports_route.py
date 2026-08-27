"""Weekly report trend aggregation regression tests."""

import unittest
from datetime import datetime, timedelta

from api.routes.reports_route import _light_trends, _weekly_recommendations


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
