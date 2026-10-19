"""逐顿矩阵（`settle_meals` / `_meal_over_list`，2026-10-19 新增）。

要钉住的是**"怎么算一顿"这条新口径**，它有五个出口：

  · 归并：相邻 ≤60 分钟算同一顿（30 分钟切太碎、120 分钟把两顿并了）；
  · 落档：按**首条记录**的小时进 早/午/晚，0–5 点并入「晚」；
  · 合并：同一天同一档有多顿 → 取**最差**那一档（不能因为其中一顿好就抹掉红）；
  · 空格：`level: None` 且 `records === 0` 才是"没记录"；
    记了但没给灯是 `level: None` + `records > 0`，两者**不许混成一个**；
  · 裁列：末尾的"今天"**完全无记录**时才裁掉，有记录就必须显示。

超标清单只验一条：**黄灯不进来**。黄是"接近上限"，与超标并列会让用户以为黄也得改。

矩阵不重算 `is_meal_on_target`（它有自己的一套测试），它走的是 `_levels_of` +
`_LEVEL_RANK` 这条"取最差"的路 —— 两条判据本来就该在"该餐算不算达标"上一致，
但矩阵要的是三档而不是布尔，所以不能直接复用那个函数。
"""
import unittest
from datetime import date

from api.routes import reports_route as rr

S = date(2026, 10, 12)
E = date(2026, 10, 18)

GREEN = ["钠:green", "糖:green", "脂肪:green"]
YELLOW = ["钠:yellow", "糖:green", "脂肪:green"]
RED = ["钠:red", "糖:green", "脂肪:green"]


def meal(ts: str, dish: str = "番茄炒蛋", lights=None) -> dict:
    return {
        "ts": ts,
        "session": "s1",
        "dish": dish,
        "lights": list(GREEN if lights is None else lights),
        "guardrails": 0,
    }


class SettleMealsTest(unittest.TestCase):
    def test_merge_within_60min(self):
        rows = [
            meal("2026-10-12T12:00:00", "A"),
            meal("2026-10-12T12:30:00", "B", YELLOW),
            meal("2026-10-12T14:30:00", "C"),  # 距上一条 120 分钟 → 另一顿
        ]
        g = rr.settle_meals(rows, S, E)
        cell = g["cells"][1][0]  # 午 · 10-12
        # 两顿都落在「午」，合并后 records 是真实条数（3），档位取最差（黄）。
        self.assertEqual(cell["records"], 3)
        self.assertEqual(cell["level"], "yellow")
        self.assertEqual(sorted(cell["dishes"]), ["A", "B", "C"])

    def test_slot_by_first_record_hour(self):
        rows = [
            meal("2026-10-12T07:30:00", "A"),
            meal("2026-10-12T12:00:00", "B"),
            meal("2026-10-12T19:00:00", "C"),
        ]
        g = rr.settle_meals(rows, S, E)
        self.assertEqual(g["slots"], ["早", "午", "晚"])
        for i in range(3):
            self.assertEqual(g["cells"][i][0]["level"], "green")

    def test_late_night_folds_into_dinner(self):
        # 0–5 点并入「晚」，不单开第四行。
        g = rr.settle_meals([meal("2026-10-12T02:00:00", "A")], S, E)
        self.assertEqual(g["cells"][2][0]["level"], "green")
        self.assertIsNone(g["cells"][0][0]["level"])

    def test_no_lights_is_none_but_keeps_records(self):
        # "记了但没给营养评估" —— level 是 None，records 是 1。
        # 前端据此画点线框，不能画成"没记录"，更不能算成绿灯。
        g = rr.settle_meals([meal("2026-10-12T12:00:00", "A", [])], S, E)
        cell = g["cells"][1][0]
        self.assertIsNone(cell["level"])
        self.assertEqual(cell["records"], 1)
        self.assertEqual(g["legend"]["none"], 21)

    def test_empty_window_is_all_none(self):
        g = rr.settle_meals([], S, E)
        self.assertEqual(g["total"], 21)
        self.assertEqual(g["legend"], {"green": 0, "yellow": 0, "red": 0, "none": 21})

    def test_same_dish_twice_keeps_worst_and_counts_both(self):
        rows = [
            meal("2026-10-12T12:00:00", "A", GREEN),
            meal("2026-10-12T12:10:00", "A", RED),
        ]
        cell = rr.settle_meals(rows, S, E)["cells"][1][0]
        self.assertEqual(cell["level"], "red")
        self.assertEqual(cell["records"], 2)  # 条数如实，不因为去重而少报
        self.assertEqual(len(cell["dishes"]), 1)  # 菜名只出现一次

    def test_red_wins_over_green_in_one_meal(self):
        rows = [
            meal("2026-10-12T12:00:00", "A", GREEN),
            meal("2026-10-12T12:20:00", "B", RED),
        ]
        self.assertEqual(rr.settle_meals(rows, S, E)["cells"][1][0]["level"], "red")

    def test_malformed_light_is_not_green(self):
        g = rr.settle_meals([meal("2026-10-12T12:00:00", "A", ["钠"])], S, E)
        self.assertNotEqual(g["cells"][1][0]["level"], "green")

    def test_drop_today_only_when_empty(self):
        g = rr.settle_meals(
            [meal("2026-10-12T12:00:00", "A")], S, date(2026, 10, 19), today=date(2026, 10, 19)
        )
        self.assertEqual(len(g["days"]), 7)
        self.assertEqual(g["dropped_today"], "2026-10-19")
        self.assertEqual(g["total"], 21)

    def test_keep_today_when_it_has_records(self):
        g = rr.settle_meals(
            [meal("2026-10-19T12:00:00", "A")], S, date(2026, 10, 19), today=date(2026, 10, 19)
        )
        self.assertEqual(len(g["days"]), 8)
        self.assertIsNone(g["dropped_today"])

    def test_bad_ts_is_skipped(self):
        rows = [meal("not-a-date", "X"), meal("2026-10-12T12:00:00", "A")]
        g = rr.settle_meals(rows, S, E)
        self.assertEqual(g["legend"]["green"], 1)
        self.assertEqual(sum(c["records"] for row in g["cells"] for c in row), 1)


class MealOverListTest(unittest.TestCase):
    def test_only_red_enters(self):
        rows = [
            meal("2026-10-12T12:00:00", "A", GREEN),
            meal("2026-10-13T12:00:00", "B", YELLOW),
            meal("2026-10-14T19:00:00", "C", RED),
        ]
        over = rr._meal_over_list(rr.settle_meals(rows, S, E))
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["date"], "2026-10-14")
        self.assertEqual(over[0]["slot"], "晚")
        self.assertEqual(over[0]["nutrients"], ["钠"])
        self.assertEqual(over[0]["bad"][0]["dish"], "C")
        self.assertTrue(over[0]["advice"])

    def test_two_nutrients_in_one_meal(self):
        rows = [meal("2026-10-14T19:00:00", "C", ["钠:red", "脂肪:red", "糖:green"])]
        over = rr._meal_over_list(rr.settle_meals(rows, S, E))
        self.assertEqual(over[0]["nutrients"], ["钠", "脂肪"])

    def test_two_dishes_same_nutrient(self):
        rows = [
            meal("2026-10-14T18:00:00", "C1", ["钠:red"]),
            meal("2026-10-14T18:20:00", "C2", ["钠:red"]),
        ]
        over = rr._meal_over_list(rr.settle_meals(rows, S, E))
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["nutrients"], ["钠"])
        self.assertEqual(len(over[0]["bad"]), 2)  # 两道菜各一条，卡上据此说"等 2 道"

    def test_no_red_no_card(self):
        over = rr._meal_over_list(
            rr.settle_meals([meal("2026-10-12T12:00:00", "A", YELLOW)], S, E)
        )
        self.assertEqual(over, [])


if __name__ == "__main__":
    unittest.main()
