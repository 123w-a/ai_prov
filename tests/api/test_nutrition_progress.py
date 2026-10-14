"""成员营养进度的达标判据与归属过滤。

判据由用户 2026-10-11 裁定（api/routes/reports_route.py `is_meal_on_target`）：
  (a) 该餐营养灯**全绿 = 达标**；
      细则 1：有项但不满三盏 → 有的项全绿仍算达标（缺项不等于不达标）；
      细则 2：**一条灯都没有 → 计入分母、不计入分子**（保守口径，不得剔除分母）；
      细则 3：`guardrails` **不参与**判据（它是"禁忌是否命中"，不是"营养是否达标"）。
分子分母都只统计**已归属到该成员**的餐次；未归属的餐次对谁都不算。
"""
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
from api.routes.reports_route import is_meal_on_target, member_nutrition_progress


def meal(member, lights, days_ago=0, guardrails=0, dish="番茄炒蛋"):
    """造一条记账记录；member 为 None 时**不写 member 键**（= 未归属）。"""
    row = {
        "ts": (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds"),
        "session": "s1",
        "dish": dish,
        "lights": list(lights),
        "guardrails": guardrails,
    }
    if member:
        row["member"] = member
    return row


class CriterionTest(unittest.TestCase):
    """三盏灯判据本身（纯函数，不过 HTTP）。"""

    def test_all_green_is_on_target(self):
        self.assertTrue(is_meal_on_target(["钠:green", "糖:green", "脂肪:green"]))

    def test_partial_lights_all_green_is_on_target(self):
        """细则 1：不满三盏但已给出的全绿 → 达标（不得因缺项判不达标）。"""
        self.assertTrue(is_meal_on_target(["钠:green"]))
        self.assertTrue(is_meal_on_target(["钠:green", "糖:green"]))

    def test_any_yellow_or_red_is_not_on_target(self):
        self.assertFalse(is_meal_on_target(["钠:green", "糖:yellow", "脂肪:green"]))
        self.assertFalse(is_meal_on_target(["钠:red"]))
        # 允许冒号前带中文标签、也允许畸形串：畸形按"不是 green"处理（宁可不达标）
        self.assertFalse(is_meal_on_target(["钠:"]))
        self.assertFalse(is_meal_on_target(["greenish"]))

    def test_no_lights_is_not_on_target_but_counts(self):
        """细则 2：没有灯 = 不达标（仍进分母；那个"仍进分母"由下面的比值测试钉住）。"""
        self.assertFalse(is_meal_on_target([]))
        self.assertFalse(is_meal_on_target(None))

    def test_guardrails_do_not_participate(self):
        """细则 3：护栏命中不改变营养达标判定，反之亦然。"""
        self.assertTrue(is_meal_on_target(["钠:green", "糖:green", "脂肪:green"]))
        self.assertFalse(is_meal_on_target(["钠:yellow"]))


class ProgressTest(unittest.TestCase):
    """分子分母与归属过滤（注入 meals，不碰真盘）。"""

    def progress(self, meals, name="小美"):
        return member_nutrition_progress({"name": name}, meals=meals)

    def test_checkable_ratio_three_of_five(self):
        """可手工核对的算例：5 条本人餐次，3 条达标 → 3/5 = 0.6。

        3 条达标 = 全绿 / 两盏全绿（缺项）/ 满三盏全绿；另外 2 条是黄灯与无灯。
        无灯那条**必须在分母里**，否则会算成 3/4 = 0.75（虚高）。
        """
        meals = [
            meal("小美", ["钠:green", "糖:green", "脂肪:green"]),
            meal("小美", ["钠:green", "糖:green"]),
            meal("小美", ["钠:green"]),
            meal("小美", ["钠:yellow", "糖:green", "脂肪:green"]),
            meal("小美", []),
        ]
        p = self.progress(meals)
        self.assertEqual(p["numerator"], 3)
        self.assertEqual(p["denominator"], 5)
        self.assertEqual(p["value"], 0.6)

    def test_no_lights_stays_in_denominator(self):
        """只有一条无灯餐次时：0/1，而不是"没数据"、也不是 1/1。"""
        p = self.progress([meal("小美", [])])
        self.assertEqual((p["numerator"], p["denominator"]), (0, 1))
        self.assertEqual(p["value"], 0.0)

    def test_unattributed_and_other_members_are_excluded(self):
        """未归属餐次对谁都不算；别人的餐不进这个人的分母。"""
        meals = [
            meal("小美", ["钠:green"]),
            meal(None, ["钠:green"]),          # 未归属
            {"ts": datetime.now().isoformat(timespec="seconds"), "session": "s1",
             "dish": "青椒肉丝", "lights": ["钠:green"], "guardrails": 0},  # 未归属（无键）
            meal("我", ["钠:green"]),          # 别人的
            meal("我", ["钠:yellow"]),
        ]
        p = self.progress(meals)
        self.assertEqual((p["numerator"], p["denominator"]), (1, 1))
        self.assertEqual(p["value"], 1.0)

    def test_zero_denominator_gives_null_value(self):
        """分母 0 → value null（不是 0%：那会被读成"一餐都没达标"）。"""
        p = self.progress([meal("我", ["钠:green"])])
        self.assertIsNone(p["value"])
        self.assertEqual(p["denominator"], 0)
        self.assertEqual(p["numerator"], 0)

    def test_missing_member_name_gives_all_null(self):
        p = member_nutrition_progress({}, meals=[meal("小美", ["钠:green"])])
        self.assertEqual(set(p), {"value", "numerator", "denominator", "period"})
        self.assertIsNone(p["value"])
        self.assertIsNone(p["numerator"])
        self.assertIsNone(p["denominator"])

    def test_out_of_window_meal_is_excluded(self):
        meals = [meal("小美", ["钠:green"]), meal("小美", ["钠:yellow"], days_ago=8)]
        p = self.progress(meals)
        self.assertEqual((p["numerator"], p["denominator"]), (1, 1))

    def test_broken_ts_is_excluded_not_guessed(self):
        meals = [meal("小美", ["钠:green"]),
                 {"ts": "不是时间", "session": "s1", "dish": "x", "lights": ["钠:green"], "member": "小美"}]
        p = self.progress(meals)
        self.assertEqual((p["numerator"], p["denominator"]), (1, 1))

    def test_guardrails_count_does_not_change_value(self):
        """护栏命中数不同、营养灯相同 → 进度相同（判据与 guardrails 无关）。"""
        a = self.progress([meal("小美", ["钠:green"], guardrails=3)])
        b = self.progress([meal("小美", ["钠:green"], guardrails=0)])
        self.assertEqual(a["value"], b["value"])
        self.assertEqual(a["value"], 1.0)

    # ── 多值用餐人（2026-10-15：一顿饭不一定全家一起吃）────────────────
    def test_multi_eater_meal_counts_for_every_member(self):
        """一顿三个人一起吃的饭 → **三个人都**进各自的分母。"一起吃饭"就是这个意思。"""
        shared = meal("小美", ["钠:green"])
        shared["members"] = ["小美", "我", "阿婆"]
        for name in ("小美", "我", "阿婆"):
            p = self.progress([shared], name=name)
            self.assertEqual((p["numerator"], p["denominator"]), (1, 1), name)
            self.assertEqual(p["value"], 1.0, name)

    def test_non_eater_of_shared_meal_is_excluded(self):
        """同桌但没被选进这一顿的人 → 分母 0、value null（**不摊派**）。"""
        shared = meal("小美", ["钠:green"])
        shared["members"] = ["小美", "我"]
        p = self.progress([shared], name="阿婆")
        self.assertIsNone(p["value"])
        self.assertEqual(p["denominator"], 0)

    def test_legacy_single_member_row_still_counts(self):
        """旧记录（单值 `member`）多值化后照旧：一个人的一顿仍进他的分母。"""
        p = self.progress([meal("小美", ["钠:green"])])
        self.assertEqual((p["numerator"], p["denominator"]), (1, 1))

    def test_members_key_overrides_member_in_denominator(self):
        """两键并存时，分母按 `members` 算——`member` 只是主成员，不是用餐人。

        这一条钉住两个键**语义不同**：`member="小美"`（这轮按她的约束求解）
        但 `members=["我"]`（实际是"我"吃）——所以进分母的是"我"，不是"小美"。
        """
        row = meal("小美", ["钠:green"])
        row["members"] = ["我"]
        self.assertEqual(self.progress([row], name="我")["denominator"], 1)
        self.assertEqual(self.progress([row], name="小美")["denominator"], 0)


class DiagnosticEndpointTest(unittest.TestCase):
    """只读诊断口 + 进度经 GET /profile 出真值（端到端，走真 HTTP 路由）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.meals_path = self.data_dir / "meals.json"
        self.meals_path.write_text(json.dumps([
            meal("小美", ["钠:green", "糖:green", "脂肪:green"]),
            meal("小美", ["钠:yellow", "糖:green"]),
            meal("小美", []),
            meal(None, ["钠:green"]),
            meal("我", ["钠:green"]),
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
        (self.data_dir / "profile.json").write_text(json.dumps({
            "version": 2,
            "active_id": "me",
            "members": [
                {"id": "me", "name": "小美", "profile": {"basic": {}, "conditions": []}},
                {"id": "m2", "name": "我", "profile": {"basic": {}, "conditions": []}},
                {"id": "m3", "name": "阿婆", "profile": {"basic": {}, "conditions": []}},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        app = FastAPI()
        app.include_router(rr.router)
        app.include_router(pr.router)
        self.client = TestClient(app)

    def test_profile_progress_is_real_numbers_for_attributed_member(self):
        fam = self.client.get("/profile").json()["data"]["family"]
        by_name = {m["name"]: m["profile"]["nutrition_progress"] for m in fam["members"]}
        self.assertEqual((by_name["小美"]["numerator"], by_name["小美"]["denominator"]), (1, 3))
        self.assertEqual(by_name["小美"]["value"], 0.3333)
        self.assertEqual((by_name["我"]["numerator"], by_name["我"]["denominator"]), (1, 1))
        # 一条都没归属给他 → 分母 0 → value null（前端「待补」）
        self.assertIsNone(by_name["阿婆"]["value"])
        self.assertEqual(by_name["阿婆"]["denominator"], 0)

    def test_attribution_diag_counts(self):
        d = self.client.get("/reports/meal-attribution").json()
        self.assertEqual(d["total"], 5)
        self.assertEqual(d["attributed"], 4)
        self.assertEqual(d["unattributed"], 1)
        self.assertEqual(d["by_member"], {"小美": 3, "我": 1})
        self.assertEqual(d["attributed_this_week"], 4)
        self.assertEqual(d["unattributed_this_week"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

