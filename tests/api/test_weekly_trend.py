"""周报页 C 结构新增的三个后端字段（2026-10-15）。

对应 `GET /reports/weekly` 新增的 `rate` / `rate_series` / `prev`，以及
`members[]` 新增的 `prev_meals` / `prev_on_target` / `hint`。

要钉住的核心只有一条，但它有六个出口：**"没有数据"不许被写成 0**。

  · 某周零记录 → 那一格 `rate: null`（不是 0.0）。折线要在这里断开；
    补成 0 会画出一条"那周吃得极差"的假谷底，而真相是"那周没记"。
  · 上周零记录 → `prev.rate: null`；前端据此写「上周无记录」而不是「+0」。
  · 成员上周零记录 → `prev_meals: 0`，前端写「较上周 —」。
  · 成员本周零记录 → `hint` 是"还没有记录"的说明，**不是**"一餐都没达标"。

其余三个出口是结构性的：

  · `prev` 窗口严格是本周窗口**整体前移 7 天**（不是自然周）。两把尺子量出的差
    会在跨周界那天突然跳一下，所以要用两个窗口的端点正面验证。
  · `rate_series` 最旧在前、本周在最后、`is_current` 只有一格，且**最后一格的
    数字必须等于平铺的 `rate`** —— 两处若是各算一遍，同一个"本周达标率"就会
    在卡片与折线末端各说各话。
  · `hint` 的优先级：红灯 > 全达标 > 持续黄 > 没给灯。刻意用真数据分别验一遍。

这一层不重算 `is_meal_on_target` 的内部（它有自己的测试），只验证这些字段
**怎么从它派生**、以及派生时有没有把"不知道"误写成"0"。
"""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from api.routes import reports_route as rr

GREEN = ["钠:green", "糖:green", "脂肪:green"]
YELLOW = ["钠:yellow", "糖:green", "脂肪:green"]
RED = ["钠:red", "糖:green", "脂肪:green"]


def meal(lights, days_ago=0, dish="番茄炒蛋", members=None):
    row = {
        "ts": (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        "session": "s1",
        "dish": dish,
        "lights": list(lights),
        "guardrails": 0,
    }
    if members is not None:
        row["members"] = list(members)
    return row


class WeeklyTrendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meals_path = Path(self.tmp.name) / "meals.json"
        p = patch.object(rr, "_MEALS", self.meals_path)
        p.start()
        self.addCleanup(p.stop)

    def write(self, rows):
        self.meals_path.write_text(
            __import__("json").dumps(rows, ensure_ascii=False), encoding="utf-8"
        )

    # ── prev 窗口 ────────────────────────────────────────────────────────

    def test_prev_window_is_exactly_seven_days_earlier(self):
        """`prev` 必须等于本周窗口整体前移 7 天，不是"上周一到上周日"。"""
        now = datetime(2026, 5, 20, 12, 0, 0)
        week_start, week_end = rr.week_window(now)
        prev_start, prev_end = rr.prev_week_window(now)
        self.assertEqual(week_start - prev_start, timedelta(days=7))
        self.assertEqual(week_end - prev_end, timedelta(days=7))

    def test_prev_meals_only_counts_last_week(self):
        self.write([
            meal(GREEN, days_ago=1),    # 本周
            meal(GREEN, days_ago=9),    # 上周
            meal(GREEN, days_ago=10),   # 上周
            meal(GREEN, days_ago=20),   # 更早，两边都不算
        ])
        r = rr.weekly_report()
        self.assertEqual(r["meals"], 1)
        self.assertEqual(r["prev"]["meals"], 2)

    def test_prev_rate_is_null_when_last_week_is_empty(self):
        """上周没记录 ⇒ `prev.rate` 是 null。用 0 当基准会算出「↑86%」，
        把一个不存在的东西当成地板。"""
        self.write([meal(GREEN, days_ago=1)])
        r = rr.weekly_report()
        self.assertEqual(r["prev"]["meals"], 0)
        self.assertIsNone(r["prev"]["rate"])
        self.assertEqual(r["rate"], 1.0)

    # ── rate_series ──────────────────────────────────────────────────────

    def test_rate_series_shape_and_order(self):
        now = datetime(2026, 5, 20, 12, 0, 0)
        series = rr._week_rate_series([], now)
        self.assertEqual(len(series), 5)
        # 最旧在前、本周在最后，且只有最后一格 is_current。
        self.assertEqual([w["is_current"] for w in series], [False] * 4 + [True])
        starts = [w["start"] for w in series]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(series[-1]["end"] > series[0]["start"])

    def test_empty_week_in_series_is_null_not_zero(self):
        """某一周零记录 ⇒ `rate: null`。**这是本文件最重要的一条**：
        补成 0.0 会让折线在那里连到谷底，读者看到的是"那周吃得极差"。"""
        self.write([meal(GREEN, days_ago=1)])
        r = rr.weekly_report()
        older = [w for w in r["rate_series"] if not w["is_current"]]
        self.assertTrue(older, "至少要有几格历史周")
        for w in older:
            self.assertEqual(w["meals"], 0)
            self.assertIsNone(w["rate"], "零记录周必须是 null 而不是 0")

    def test_last_series_cell_equals_flat_rate(self):
        """折线末端与卡片上的「本周达标率」必须是同一个数。
        两处各算一遍的话，同一个概念会在两个组件里各说各话。"""
        self.write([
            meal(GREEN, days_ago=1),
            meal(YELLOW, days_ago=2),
            meal(GREEN, days_ago=3),
        ])
        r = rr.weekly_report()
        last = r["rate_series"][-1]
        self.assertTrue(last["is_current"])
        self.assertEqual(last["rate"], r["rate"])
        self.assertEqual(last["meals"], r["meals"])
        self.assertEqual(last["on_target"], 2)
        self.assertAlmostEqual(r["rate"], 2 / 3, places=4)

    def test_bad_ts_does_not_break_the_series(self):
        """一条坏时间戳不该让整页读不出来（`_in_range` 必须吞掉它）。"""
        rows = [meal(GREEN, days_ago=1), {"ts": "not-a-date", "dish": "x", "lights": GREEN}]
        self.write(rows)
        r = rr.weekly_report()
        self.assertTrue(r["has_data"])
        self.assertTrue(all(w["rate"] is None or 0 <= w["rate"] <= 1 for w in r["rate_series"]))

    # ── 成员 hint ────────────────────────────────────────────────────────

    def test_hint_when_no_meals(self):
        h = rr._member_hint("小美", [])
        self.assertIn("还没有", h)
        self.assertNotIn("达标", h.replace("达标率", ""))

    def test_hint_prioritises_red(self):
        """有红灯就先讲红灯——那是唯一需要马上改的事。"""
        h = rr._member_hint("我", [meal(RED), meal(GREEN)])
        self.assertIn("钠", h)
        self.assertIn("红灯", h)

    def test_hint_all_green(self):
        h = rr._member_hint("我", [meal(GREEN), meal(GREEN)])
        self.assertIn("达标", h)
        self.assertNotIn("红灯", h)

    def test_hint_yellow_when_nothing_worse(self):
        h = rr._member_hint("我", [meal(YELLOW), meal(YELLOW)])
        self.assertIn("钠", h)
        self.assertIn("基本达标", h)

    def test_hint_without_lights_is_honest(self):
        """记了餐但没给灯：要说"没给灯"，不能说成达标或未达标。"""
        h = rr._member_hint("我", [meal([])])
        self.assertIn("营养灯", h)

    # ── 成员较上周 ───────────────────────────────────────────────────────

    def test_member_prev_only_counts_own_meals(self):
        """成员上周数只算**他自己参与**的餐，与家庭数不混用。"""
        self.write([
            # 上周「我」那条刻意给黄灯：这样 prev_meals=1 而 prev_on_target=0，
            # 两个字段的差别才被真的验到（都记成绿的会退化成同一个数）。
            meal(YELLOW, days_ago=9, members=["我"]),
            meal(GREEN, days_ago=9, members=["小美"]),
            meal(GREEN, days_ago=1, members=["我", "小美"]),
        ])
        pr = patch("api.routes.preferences_route._read_family",
                   lambda: {"members": [{"name": "我"}, {"name": "小美"}], "active_id": "m1"})
        pr.start()
        self.addCleanup(pr.stop)
        r = rr.weekly_report()
        by = {m["name"]: m for m in r["members"]}
        self.assertEqual(by["我"]["prev_meals"], 1)
        self.assertEqual(by["小美"]["prev_meals"], 1)
        self.assertEqual(by["我"]["meals"], 1)
        # prev_meals 与 prev_on_target 分开表达"上周记了几顿"和"上周成没成"：
        # 这里 1 与 0 的差就是"记了但没达标"那一档。把两个字段合并成一个百分比
        # 会让"上周 1 顿没达标"和"上周没记录"看起来一样。
        self.assertEqual(by["我"]["prev_meals"], 1)
        self.assertEqual(by["我"]["prev_on_target"], 0)
        self.assertEqual(by["小美"]["prev_on_target"], 1)

    def test_member_hint_present_for_zero_meal_member(self):
        """本周一顿没记到的成员仍有卡片、且有 hint —— 不是被跳过。"""
        self.write([meal(GREEN, days_ago=1, members=["我"])])
        pr = patch("api.routes.preferences_route._read_family",
                   lambda: {"members": [{"name": "我"}, {"name": "小美"}], "active_id": "m1"})
        pr.start()
        self.addCleanup(pr.stop)
        r = rr.weekly_report()
        names = [m["name"] for m in r["members"]]
        self.assertIn("小美", names)
        mei = next(m for m in r["members"] if m["name"] == "小美")
        self.assertEqual(mei["meals"], 0)
        self.assertTrue(mei["hint"])


if __name__ == "__main__":
    unittest.main()


