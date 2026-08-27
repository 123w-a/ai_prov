"""Weekly report trend aggregation regression tests."""

import unittest
from datetime import datetime, timedelta

from api.routes.reports_route import _light_trends


def meal(ts, lights):
    return {"ts": ts.isoformat(timespec="seconds"), "lights": lights}


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
