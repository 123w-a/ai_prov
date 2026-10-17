"""餐次成员归属：记账落键 + 老数据回填（2026-10-11 用户裁定的两条加法）。

钉住的三件事：
  1. `record_meal` 落新键 `member`，且**既有五个键语义不变**（对不上就写不进去）；
  2. 回填只在 `session + dish` **唯一**匹配时才写；多解/匹配不上/命中无成员 → 留空；
  3. 回填幂等、并且**只新增 member 一个键**（既有键逐键比对，值一字不动）。
"""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from api.routes import reports_route as rr
from storage.meal_attribution import (
    attribution_stats,
    backfill_meals,
    backfill_report,
    collect_session_answers,
    eaters_of,
    meal_dish_of,
    member_of,
    members_of,
    resolve_member,
)


def answer(dish="番茄炒蛋", member="小美", lights=None, members=None):
    """造一条结构化答案。`members` **只有显式传了才落键**——这样既有测试的
    "键集合"断言（不信 `members`）继续成立，不会因为造数工具换了而松掉。"""
    payload = {"recipes": [{"name": dish}], "primary_member": member,
               "health_lights": lights if lights is not None else [{"label": "钠", "level": "green"}],
               "guardrails": []}
    if members is not None:
        payload["members"] = members
    return payload


class RecordMealTest(unittest.TestCase):
    """记账新增键 member（既有键的算法与取值不变）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meals_path = Path(self.tmp.name) / "meals.json"
        p = patch.object(rr, "_MEALS", self.meals_path)
        p.start()
        self.addCleanup(p.stop)

    def rows(self):
        return json.loads(self.meals_path.read_text(encoding="utf-8"))

    def test_answer_member_is_persisted(self):
        rr.record_meal("s1", answer(member="小美"))
        row = self.rows()[0]
        self.assertEqual(row["member"], "小美")
        self.assertEqual(
            sorted(row), ["dish", "guardrails", "lights", "member", "session", "ts"]
        )

    def test_missing_primary_member_writes_no_key(self):
        """没有 primary_member → **不写这个键**（= 未归属），不写 null、不写空串。"""
        rr.record_meal("s1", answer(member=None))
        row = self.rows()[0]
        self.assertNotIn("member", row)

    def test_existing_five_keys_are_unchanged(self):
        """既有键逐字不变：dish 截 40 字符、lights 拼 label:level、guardrails 计数。"""
        payload = answer(dish="很长的菜名" * 10, member="小美")
        payload["health_lights"] = [{"label": "钠", "level": "green"}, {"label": "糖", "level": "yellow"}]
        payload["guardrails"] = [{"status": "pass"}, {"status": "warn"}]
        rr.record_meal("s9", payload)
        row = self.rows()[0]
        self.assertEqual(row["session"], "s9")
        self.assertEqual(row["dish"], ("很长的菜名" * 10)[:40])
        self.assertEqual(row["lights"], ["钠:green", "糖:yellow"])
        self.assertEqual(row["guardrails"], 2)
        datetime.fromisoformat(row["ts"])  # ts 仍是 ISO 秒精度

    def test_no_recipes_still_skipped(self):
        rr.record_meal("s1", {"primary_member": "小美"})
        self.assertFalse(self.meals_path.exists())


class AttributionHelpersTest(unittest.TestCase):
    def test_meal_dish_of_matches_record_meal_algorithm(self):
        """菜名算法只有一处：40 字符截断，不 strip（与落库逐字一致）。"""
        self.assertEqual(meal_dish_of({"recipes": [{"name": " 番茄炒蛋 "}]}), " 番茄炒蛋 ")
        self.assertEqual(meal_dish_of({"recipes": [{"name": "x" * 50}]}), "x" * 40)
        self.assertIsNone(meal_dish_of({"recipes": []}))
        self.assertIsNone(meal_dish_of({"recipes": ["番茄炒蛋"]}))  # 非 dict
        self.assertIsNone(meal_dish_of("纯文本回答"))

    def test_member_of_trims_and_rejects_junk(self):
        self.assertEqual(member_of({"primary_member": " 小美 "}), "小美")
        self.assertIsNone(member_of({"primary_member": ""}))
        self.assertIsNone(member_of({"primary_member": None}))
        self.assertIsNone(member_of({"primary_member": 3}))
        self.assertIsNone(member_of({}))

    def test_resolve_member_unique_match(self):
        answers = [answer("番茄炒蛋", "小美"), answer("青椒肉丝", "我")]
        self.assertEqual(resolve_member("番茄炒蛋", answers), ("小美", "matched"))
        self.assertEqual(resolve_member("红烧肉", answers), (None, "unmatched"))

    def test_resolve_member_ambiguous_even_same_person(self):
        """命中两条就留空——多解本身就是不确定性，宁可少归一条也不摊派。"""
        answers = [answer("番茄炒蛋", "小美"), answer("番茄炒蛋", "小美")]
        self.assertEqual(resolve_member("番茄炒蛋", answers), (None, "ambiguous"))
        answers = [answer("番茄炒蛋", "小美"), answer("番茄炒蛋", "我")]
        self.assertEqual(resolve_member("番茄炒蛋", answers), (None, "ambiguous"))

    def test_resolve_member_hit_without_member_key(self):
        answers = [answer("番茄炒蛋", None)]
        self.assertEqual(resolve_member("番茄炒蛋", answers), (None, "no_member"))


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()

    def write_session(self, sid, messages):
        (self.sessions / f"{sid}.json").write_text(
            json.dumps({"session_id": sid, "messages": messages}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_unique_match_is_filled_and_others_left_blank(self):
        self.write_session("s1", [
            {"id": 1, "answer": json.dumps(answer("番茄炒蛋", "小美"), ensure_ascii=False)},
            {"id": 2, "answer": "纯文本，不入候选池"},
        ])
        self.write_session("s2", [
            {"id": 1, "answer": json.dumps(answer("青椒肉丝", "小美"), ensure_ascii=False)},
            {"id": 2, "answer": json.dumps(answer("青椒肉丝", "我"), ensure_ascii=False)},
        ])
        meals = [
            {"ts": "2026-10-11T12:00:00", "session": "s1", "dish": "番茄炒蛋", "lights": [], "guardrails": 0},
            {"ts": "2026-10-11T12:00:00", "session": "s2", "dish": "青椒肉丝", "lights": [], "guardrails": 0},
            {"ts": "2026-10-11T12:00:00", "session": "s3", "dish": "红烧肉", "lights": [], "guardrails": 0},
            {"ts": "2026-10-11T12:00:00", "session": "s1", "dish": "没有任何会话提过的菜", "lights": [], "guardrails": 0},
        ]
        answers = collect_session_answers(self.sessions)
        updated, report = backfill_meals(meals, answers)
        self.assertEqual(updated[0]["member"], "小美")
        self.assertNotIn("member", updated[1])  # 多解 → 留空
        self.assertNotIn("member", updated[2])  # 会话不存在 → 留空
        self.assertNotIn("member", updated[3])  # 同会话但菜名不匹配 → 留空
        self.assertEqual(report["filled"], 1)
        self.assertEqual(report["ambiguous"], 1)
        self.assertEqual(report["unmatched"], 2)
        self.assertEqual(report["unattributed"], 3)

    def test_backfill_only_adds_member_key(self):
        """逐键比对：既有键的值与顺序都不许动。"""
        self.write_session("s1", [{"id": 1, "answer": json.dumps(answer("番茄炒蛋", "小美"), ensure_ascii=False)}])
        original = {"ts": "2026-10-11T12:00:00", "session": "s1", "dish": "番茄炒蛋",
                    "lights": ["钠:green", "糖:yellow"], "guardrails": 2}
        answers = collect_session_answers(self.sessions)
        updated, _ = backfill_meals([dict(original)], answers)
        self.assertEqual(list(updated[0])[: len(original)], list(original))
        for key, value in original.items():
            self.assertEqual(updated[0][key], value)
        self.assertEqual(list(updated[0])[-1], "member")
        self.assertEqual(len(updated[0]), len(original) + 1)

    def test_backfill_is_idempotent(self):
        self.write_session("s1", [{"id": 1, "answer": json.dumps(answer("番茄炒蛋", "小美"), ensure_ascii=False)}])
        meals = [{"ts": "2026-10-11T12:00:00", "session": "s1", "dish": "番茄炒蛋", "lights": [], "guardrails": 0}]
        answers = collect_session_answers(self.sessions)
        first, r1 = backfill_meals(meals, answers)
        second, r2 = backfill_meals(first, answers)
        self.assertEqual(r1["filled"], 1)
        self.assertEqual(r2["filled"], 0)
        self.assertEqual(r2["already"], 1)
        self.assertEqual(second, first)

    def test_backfill_report_writes_file_only_on_apply(self):
        self.write_session("s1", [{"id": 1, "answer": json.dumps(answer("番茄炒蛋", "小美"), ensure_ascii=False)}])
        meals_path = self.root / "meals.json"
        meals_path.write_text(json.dumps(
            [{"ts": "2026-10-11T12:00:00", "session": "s1", "dish": "番茄炒蛋", "lights": [], "guardrails": 0}],
            ensure_ascii=False), encoding="utf-8")

        dry = backfill_report(meals_path=meals_path, sessions_dir=self.sessions, dry_run=True)
        self.assertEqual(dry["filled"], 1)
        self.assertNotIn("member", json.loads(meals_path.read_text(encoding="utf-8"))[0])

        applied = backfill_report(meals_path=meals_path, sessions_dir=self.sessions)
        self.assertEqual(applied["filled"], 1)
        self.assertEqual(json.loads(meals_path.read_text(encoding="utf-8"))[0]["member"], "小美")

        again = backfill_report(meals_path=meals_path, sessions_dir=self.sessions)
        self.assertEqual((again["filled"], again["already"]), (0, 1))

    def test_attribution_stats(self):
        stats = attribution_stats([
            {"member": "小美"}, {"member": "小美"}, {"member": "我"}, {},
        ])
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["attributed"], 3)
        self.assertEqual(stats["unattributed"], 1)
        self.assertEqual(stats["by_member"], {"小美": 2, "我": 1})


class MultiMemberTest(unittest.TestCase):
    """一顿饭不一定全家一起吃（2026-10-15 用户裁决）：多值用餐人 `members`。

    `member`（主成员：这一轮的健康约束按谁求解）与 `members`（本餐实际用餐人：
    归属统计与周报分母按谁算）是**两个概念**，一顿饭可以三个人吃而只按一个人求解。
    本类钉住的四件事：
      1. 两个键并存、互不冒充——尤其**不拿 `member` 去补 `members`**；
      2. 旧记录（单值 `member`）回落成一个用餐人，多值化不动它；
      3. 落库前清洗：去空白、丢非字符串、同名去重保序；
      4. `attribution_stats` 的**条数与人次必须分开**——一餐两人时 `by_member`
         加出 2，而记录只有 1 条；上一版 `attributed = sum(by_member.values())`
         在多值下会把条数虚报成人次。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meals_path = Path(self.tmp.name) / "meals.json"
        p = patch.object(rr, "_MEALS", self.meals_path)
        p.start()
        self.addCleanup(p.stop)

    def rows(self):
        return json.loads(self.meals_path.read_text(encoding="utf-8"))

    # ── 清洗与回落 ────────────────────────────────────────────────
    def test_members_of_cleans_and_dedupes_in_order(self):
        """去空白、丢非字符串、同名只留一次；**顺序即用户选择的顺序**。"""
        self.assertEqual(
            members_of({"members": [" 小美 ", "我", "小美", "", 3, None]}),
            ["小美", "我"],
        )

    def test_members_of_rejects_missing_and_malformed(self):
        for bad in ({}, {"members": None}, {"members": "小美"}, {"members": []}, "纯文本"):
            self.assertEqual(members_of(bad), [], repr(bad))

    def test_eaters_of_prefers_members_then_falls_back_to_member(self):
        self.assertEqual(
            eaters_of({"members": ["小美", "我"], "member": "小美"}), ["小美", "我"]
        )
        self.assertEqual(eaters_of({"member": "小美"}), ["小美"])  # 旧记录
        self.assertEqual(eaters_of({"members": []}), [])
        self.assertEqual(eaters_of({"members": [] , "member": "小美"}), ["小美"])
        self.assertEqual(eaters_of({}), [])
        self.assertEqual(eaters_of("坏了"), [])

    # ── 落库 ──────────────────────────────────────────────────────
    def test_members_are_persisted_beside_member(self):
        """两个键并存、语义不同。"""
        rr.record_meal("s1", answer(member="小美", members=["小美", "我"]))
        row = self.rows()[0]
        self.assertEqual(row["member"], "小美")
        self.assertEqual(row["members"], ["小美", "我"])

    def test_call_argument_members_win_over_answer(self):
        """前端从请求带来的用餐人优先于 answer 自带的（首屏选择器是权威来源）。"""
        rr.record_meal("s1", answer(member="小美", members=["小美"]), ["小美", "我"])
        self.assertEqual(self.rows()[0]["members"], ["小美", "我"])

    def test_member_is_not_used_to_fabricate_members(self):
        """**不拿 `member` 补 `members`**：那是把"只登记了一个人"说成"只有一个人吃"。

        这条与 `RecordMealTest.test_answer_member_is_persisted` 的键集合断言是一对：
        那条钉住"没有 members 就别写"，这条钉住"有 member 也不许拿它顶替"。
        """
        rr.record_meal("s1", answer(member="小美"))
        row = self.rows()[0]
        self.assertNotIn("members", row)
        self.assertEqual(row["member"], "小美")

    def test_empty_members_writes_no_key(self):
        rr.record_meal("s1", answer(member="小美", members=[]))
        self.assertNotIn("members", self.rows()[0])

    def test_duplicate_members_deduped_before_persist(self):
        """这层不靠前端守规矩：重复的人会把周报分母算重。"""
        rr.record_meal("s1", answer(members=["小美", "我", "小美"]))
        self.assertEqual(self.rows()[0]["members"], ["小美", "我"])

    # ── 统计：条数与人次必须分开 ──────────────────────────────────
    def test_stats_separate_record_count_from_person_count(self):
        """一餐两人 → `by_member` 加出 2，但记录只有 1 条。两个数都要，不能混。"""
        stats = attribution_stats([
            {"member": "小美", "members": ["小美", "我"]},
            {"member": "我", "members": ["我"]},
            {},
        ])
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["attributed"], 2)          # 条数
        self.assertEqual(stats["unattributed"], 1)
        self.assertEqual(stats["by_member"], {"我": 2, "小美": 1})  # 人次

    def test_stats_treats_legacy_single_member_as_one_eater(self):
        stats = attribution_stats([{"member": "小美"}])
        self.assertEqual(stats["attributed"], 1)
        self.assertEqual(stats["by_member"], {"小美": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)


