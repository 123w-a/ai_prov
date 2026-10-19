"""domain/intake_tips.py 的单元测试：建议必须可追溯到真实统计量。

这几条是这套逻辑的底线，缺一条就会变成「看起来专业的套话」：
  · 样本不足要直说，不能拿 3 餐推方向性结论；
  · 只取最近 6 餐，更早的账不能影响今天的结论；
  · 畸形灯色（历史遗留的「脂肪:」）要忽略而不是当成红灯；
  · 坏时间戳不能让整块建议崩掉；
  · 凑不出三条就出两条，不补位。
"""
import unittest
from datetime import datetime, timedelta

from domain.intake_tips import MAX_MEALS, build_intake_tips


def _meal(ts, lights, dish="番茄炒蛋"):
    return {"ts": ts.isoformat(), "dish": dish, "lights": list(lights)}


class TestIntakeTips(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 12, 0)

    def _seq(self, specs):
        """从最近往前生成，specs[0] 是最新一餐。"""
        out = []
        for i, (lights, dish) in enumerate(specs):
            out.append(_meal(self.now - timedelta(hours=i * 6), lights, dish))
        return out

    def test_insufficient_samples_says_so(self):
        meals = self._seq([(["钠:green", "糖:green", "脂肪:green"], "番茄炒蛋")] * 3)
        r = build_intake_tips(meals, now=self.now)
        self.assertEqual(r["basis"], "insufficient")
        self.assertEqual(len(r["tips"]), 1)
        self.assertEqual(r["tips"][0]["kind"], "sample")
        self.assertIn("样本还不够", r["tips"][0]["text"])

    def test_sodium_over_threshold_produces_intake_tip(self):
        # 6 餐里 4 餐钠黄/红 => 67%，超过 50% 阈值
        specs = [["钠:yellow", "糖:green", "脂肪:green"]] * 4 + \
                [["钠:green", "糖:green", "脂肪:green"]] * 2
        meals = self._seq([(s, "番茄炒蛋") for s in specs])
        r = build_intake_tips(meals, now=self.now)
        self.assertEqual(r["basis"], "ok")
        self.assertEqual(r["metrics"]["钠"]["off"], 4)
        self.assertEqual(r["metrics"]["钠"]["total"], 6)
        first = r["tips"][0]
        self.assertEqual(first["kind"], "intake")
        self.assertEqual(first["metric"], "钠")
        self.assertIn("4 餐", first["text"])
        self.assertEqual(first["detail"], "越线 4 / 样本 6")

    def test_all_green_gives_positive_only(self):
        meals = self._seq([(["钠:green", "糖:green", "脂肪:green"], f"菜{i}")
                           for i in range(6)])
        r = build_intake_tips(meals, now=self.now)
        kinds = [t["kind"] for t in r["tips"]]
        self.assertIn("positive", kinds)
        self.assertNotIn("intake", kinds)

    def test_problem_also_mentions_what_is_fine(self):
        specs = [["钠:yellow", "糖:green", "脂肪:green"]] * 4 + \
                [["钠:green", "糖:green", "脂肪:green"]] * 2
        meals = self._seq([(s, f"菜{i}") for i, s in enumerate(specs)])
        r = build_intake_tips(meals, now=self.now)
        texts = " ".join(t["text"] for t in r["tips"])
        self.assertIn("钠", texts)
        self.assertTrue(any(t["kind"] == "positive" for t in r["tips"]),
                        "有问题时也应点出糖/脂肪是好的")

    def test_only_latest_six_meals_count(self):
        """第 7 餐及更早的账必须不影响结论。"""
        newest = [["钠:green", "糖:green", "脂肪:green"]] * 6
        older = [["钠:red", "糖:red", "脂肪:red"]] * 10
        meals = self._seq([(s, "菜") for s in newest + older])
        r = build_intake_tips(meals, now=self.now)
        self.assertEqual(r["meals"], MAX_MEALS)
        self.assertEqual(r["metrics"]["钠"]["total"], MAX_MEALS)
        self.assertEqual(r["metrics"]["钠"]["off"], 0)

    def test_malformed_light_is_ignored(self):
        meals = self._seq([(["钠:green", "糖:green", "脂肪:", "脂肪:green"], "菜")] * 6)
        r = build_intake_tips(meals, now=self.now)
        # 「脂肪:」不是合法灯色，应被丢弃；脂肪样本数仍为 6
        self.assertEqual(r["metrics"]["脂肪"]["total"], 6)
        self.assertEqual(r["metrics"]["脂肪"]["off"], 0)

    def test_broken_timestamp_does_not_crash(self):
        meals = [
            {"ts": "不是时间", "dish": "坏记录", "lights": ["钠:red"]},
            _meal(self.now, ["钠:green", "糖:green", "脂肪:green"]),
        ]
        r = build_intake_tips(meals, now=self.now)
        self.assertIn(r["basis"], ("ok", "insufficient"))
        self.assertIsInstance(r["tips"], list)

    def test_variety_needs_three_repeats(self):
        """6 餐里同一道菜 2 次不叫集中，3 次才提。"""
        two = self._seq([(["钠:green"], "宫保鸡丁"),
                         (["钠:green"], "宫保鸡丁")] +
                        [(["钠:green"], f"菜{i}") for i in range(4)])
        self.assertFalse(any(t["kind"] == "variety"
                             for t in build_intake_tips(two, now=self.now)["tips"]))

        three = self._seq([(["钠:green"], "宫保鸡丁")] * 3 +
                          [(["钠:green"], f"菜{i}") for i in range(3)])
        varieties = [t for t in build_intake_tips(three, now=self.now)["tips"]
                     if t["kind"] == "variety"]
        self.assertEqual(len(varieties), 1)
        self.assertIn("宫保鸡丁", varieties[0]["text"])

    def test_tips_never_exceed_three(self):
        specs = [["钠:red", "糖:red", "脂肪:red"]] * 6
        meals = self._seq([(s, "同一道菜") for s in specs])
        r = build_intake_tips(meals, now=self.now)
        self.assertLessEqual(len(r["tips"]), 3)

    def test_empty_input(self):
        r = build_intake_tips([], now=self.now)
        self.assertEqual(r["basis"], "insufficient")
        self.assertEqual(len(r["tips"]), 1)

    def test_no_severity_field_leaks(self):
        specs = [["钠:yellow", "糖:green", "脂肪:green"]] * 6
        meals = self._seq([(s, "菜") for s in specs])
        for tip in build_intake_tips(meals, now=self.now)["tips"]:
            self.assertNotIn("_severity", tip)


if __name__ == "__main__":
    unittest.main()
