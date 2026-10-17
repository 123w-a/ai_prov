"""成员周报接口（2026-10-15 新增，多值用餐人配套）。

要钉住的三件事：
  1. 只统计**这位成员确实参与了**的餐（`members` 新键优先、旧键 `member` 回落）；
  2. 多位成员一起吃的餐，**每个人各自的分母都要 +1**——这正是"一起吃饭"的定义；
  3. 没记到餐 / 名字陌生 / 名字为空 → `has_data: false` 加一句诚实说明，
     绝不返回 0%（那会被读成"一餐都没达标"）。

趋势、灯分布、建议、逐餐明细都**复用家庭版同一批函数**，所以这里不重算它们的
内部算法（那些已有各自的测试），只验"入参确实被筛成了这位成员的餐"。
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from api.routes import reports_route as rr


def meal(member, lights, days_ago=0, dish="番茄炒蛋", members=None):
    """造一条记账记录；`member` 为 None 时**不写 member 键**（= 未归属）；
    `members` 只有显式传了才写（旧记录形态）。"""
    row = {
        "ts": (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        "session": "s1",
        "dish": dish,
        "lights": list(lights),
        "guardrails": 0,
    }
    if member:
        row["member"] = member
    if members is not None:
        row["members"] = members
    return row


class MemberWeeklyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meals_path = Path(self.tmp.name) / "meals.json"
        for p in (
            patch.object(rr, "_MEALS", self.meals_path),
            # 菜品成品图会去查 dish_assets，与本节无关，直接置空。
            patch.object(rr, "_dish_images", lambda names: {}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def write(self, rows):
        self.meals_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def call(self, name):
        return rr.member_weekly_report(name=name)

    # ── 归属筛选 ──────────────────────────────────────────────────
    def test_only_own_meals_are_counted(self):
        self.write([
            meal("小美", ["钠:green"]),
            meal("小美", ["钠:yellow"]),
            meal("我", ["钠:green"]),
            meal(None, ["钠:green"]),                      # 未归属
        ])
        r = self.call("小美")
        self.assertTrue(r["has_data"])
        self.assertEqual(r["meals"], 2)
        self.assertEqual((r["progress"]["numerator"], r["progress"]["denominator"]), (1, 2))

    def test_shared_meal_counts_for_everyone(self):
        """一顿三个人一起吃的饭 → 三个人各自的分母都 +1。"""
        shared = meal("小美", ["钠:green"], members=["小美", "我", "阿婆"])
        self.write([shared])
        for who in ("小美", "我", "阿婆"):
            r = self.call(who)
            self.assertEqual(
                (r["progress"]["numerator"], r["progress"]["denominator"]), (1, 1), who
            )

    def test_non_eater_of_shared_meal_gets_nothing(self):
        """同桌但没被选进这一顿的人 → has_data false，**不摊派**。"""
        self.write([meal("小美", ["钠:green"], members=["小美", "我"])])
        r = self.call("阿婆")
        self.assertFalse(r["has_data"])
        self.assertIsNone(r["progress"]["value"])
        self.assertEqual(r["progress"]["denominator"], 0)

    def test_legacy_single_member_row_still_counts(self):
        """旧记录（单值 `member`，无 `members` 键）照旧进这个人的分母。"""
        self.write([meal("小美", ["钠:green"])])
        r = self.call("小美")
        self.assertTrue(r["has_data"])
        self.assertEqual(r["meals"], 1)

    def test_members_key_overrides_member(self):
        """两键并存时按 `members` 算：`member` 只是主成员，不是用餐人。"""
        self.write([meal("小美", ["钠:green"], members=["我"])])
        self.assertTrue(self.call("我")["has_data"])
        self.assertFalse(self.call("小美")["has_data"])

    def test_out_of_window_meals_excluded(self):
        self.write([meal("小美", ["钠:green"]), meal("小美", ["钠:green"], days_ago=8)])
        self.assertEqual(self.call("小美")["meals"], 1)

    def test_broken_ts_does_not_crash(self):
        """坏 `ts` 不算进来，也**不许把接口打崩**。"""
        self.write([
            meal("小美", ["钠:green"]),
            {"ts": "不是时间", "session": "s1", "dish": "x",
             "lights": ["钠:green"], "member": "小美"},
        ])
        self.assertEqual(self.call("小美")["meals"], 1)

    # ── 缺失状态（诚实纪律）──────────────────────────────────────
    def test_no_meals_gives_honest_missing_state(self):
        self.write([meal("我", ["钠:green"])])
        r = self.call("小美")
        self.assertFalse(r["has_data"])
        self.assertIn("小美", r["message"])
        self.assertEqual(r["meals"], 0)
        self.assertIsNone(r["progress"]["value"])       # 分母 0 → null（不是 0%）
        self.assertEqual(r["progress"]["denominator"], 0)

    def test_unknown_name_does_not_raise(self):
        self.write([meal("小美", ["钠:green"])])
        r = self.call("查无此人")
        self.assertFalse(r["has_data"])
        self.assertEqual(r["meals"], 0)

    def test_blank_name_does_not_raise(self):
        self.write([meal("小美", ["钠:green"])])
        r = self.call("   ")
        self.assertFalse(r["has_data"])
        self.assertEqual(r["name"], "")

    # ── 与家庭版共用同一批函数：验入参筛对了 ────────────────────────
    def test_series_and_detail_cover_only_own_meals(self):
        self.write([
            meal("小美", ["钠:green"], dish="番茄炒蛋"),
            meal("我", ["钠:red"], dish="青椒肉丝"),
        ])
        r = self.call("小美")
        self.assertEqual(len(r["meals_detail"]), 1)
        self.assertEqual(r["meals_detail"][0]["dish"], "番茄炒蛋")
        # 她的序列里不该出现别人那顿的红灯
        levels = [l["level"] for m in r["meals_detail"] for l in m["lights"]]
        self.assertEqual(levels, ["green"])
        # 序列仍是 7 天满槽（缺观察的槽是 None，前端断线不插值）
        self.assertIn("钠", r["light_series"])
        self.assertEqual(len(r["light_series"]["钠"]), 7)

    def test_meals_detail_carries_members(self):
        """明细带 `members`：前端能显示"这一顿还有谁一起吃"。"""
        self.write([meal("小美", ["钠:green"], members=["小美", "我"])])
        row = self.call("小美")["meals_detail"][0]
        self.assertEqual(row["members"], ["小美", "我"])
        self.assertTrue(row["on_target"])

    def test_sparse_history_gets_no_directional_advice(self):
        """样本太少 → 趋势 `insufficient`，**不给方向性建议**（不编趋势）。"""
        self.write([meal("小美", ["钠:green"])])
        r = self.call("小美")
        self.assertTrue(all(t == "insufficient" for t in r["light_trends"].values()))
        self.assertEqual(r["recommendations"], [])

    def test_period_and_days_shape(self):
        self.write([meal("小美", ["钠:green"])])
        r = self.call("小美")
        self.assertEqual(len(r["days"]), 7)
        self.assertEqual(set(r["period"]), {"start", "end"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
