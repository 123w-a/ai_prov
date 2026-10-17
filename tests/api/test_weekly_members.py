"""周报页的成员卡片（2026-10-15，`GET /reports/weekly` 新增 `members` 字段）。

要钉住五件事：

  1. **名单来自档案**（`profile.json`），不是从 meals 里出现过的名字反推——
     本周一顿都没记到的成员**仍然要在列表里**且 `meals: 0`（不画这张卡等于说
     这个人不在这个家里）；档案里没有的人即使有餐也不出现（他不在这个家）。
  2. 每位成员只算**自己参与**的餐（`eaters_of` 的新键优先、旧键回落）。
  3. **卡片上的数字必须等于 `/reports/member-weekly` 的详情**。这条是本文件最主要
     的目的：两处各算一遍是"同一份数据两个界面各说各话"的经典入口，而单看任一处
     都自洽、很难发现。这里用两个端点互相对照把它钉死。
  4. `shared` 只统计**多人同桌**的顿数（独食不计）。
  5. 没有档案时返回空列表，**不抛异常**——读不到档案不是周报的错。

这一层刻意不重算达标与灯的内部算法（`is_meal_on_target` / Counter 口径各有其测试）。
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from api.routes import preferences_route as pr
from api.routes import reports_route as rr


def meal(lights, days_ago=0, dish="番茄炒蛋", member=None, members=None):
    """造一条记账记录。`member`/`members` 只有显式传了才写键（缺键 = 未归属）。"""
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


class WeeklyMemberCardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.meals_path = Path(self.tmp.name) / "meals.json"
        for p in (
            patch.object(rr, "_MEALS", self.meals_path),
            patch.object(rr, "_dish_images", lambda names: {}),
        ):
            p.start()
            self.addCleanup(p.stop)

    def household(self, names):
        """把档案里的成员名固定下来。`_member_week_rows` 是函数内 import
        `preferences_route._read_family`，所以 patch 那个模块的属性就能拦住它。"""
        p = patch.object(pr, "_read_family", lambda: {"members": [{"name": n} for n in names]})
        p.start()
        self.addCleanup(p.stop)

    def write(self, rows):
        self.meals_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def cards(self):
        """走真实路径：read_meals → 窗口筛 → 摘要。"""
        data = rr.read_meals()
        since, _ = rr.week_window()
        recent = [m for m in data if isinstance(m, dict) and rr._in_week(m, since)]
        return rr._member_week_rows(recent)

    # ── 名单来源：档案，不是 meals ──────────────────────────────
    def test_member_with_no_meals_still_gets_a_card(self):
        """本周一顿都没记到的成员照样出卡，meals=0 —— 不许从列表里消失。"""
        self.household(["小美", "我"])
        self.write([meal(["钠:green"], members=["我"])])
        cards = self.cards()
        self.assertEqual([c["name"] for c in cards], ["小美", "我"])
        self.assertEqual(cards[0]["meals"], 0)
        self.assertEqual(cards[0]["lights"], {})
        self.assertEqual(cards[0]["on_target"], 0)
        self.assertEqual(cards[1]["meals"], 1)

    def test_names_come_from_profile_order_not_from_meals(self):
        """顺序照档案来，不按 meals 里谁出现得早。"""
        self.household(["我", "小美"])
        self.write([
            meal(["钠:green"], members=["小美"]),
            meal(["钠:green"], members=["我"]),
        ])
        self.assertEqual([c["name"] for c in self.cards()], ["我", "小美"])

    def test_person_not_in_profile_gets_no_card(self):
        """有餐但不在档案里的人不出现——他不在这个家，不是"遗漏了"。"""
        self.household(["我"])
        self.write([meal(["钠:green"], members=["隔壁老王"])])
        cards = self.cards()
        self.assertEqual([c["name"] for c in cards], ["我"])
        self.assertEqual(cards[0]["meals"], 0)

    def test_no_profile_returns_empty_without_raising(self):
        """读不到档案 → 空列表。周报不因为它读不出来而 500。"""
        self.household([])
        self.write([meal(["钠:green"], members=["我"])])
        self.assertEqual(self.cards(), [])

    def test_blank_name_in_profile_is_skipped(self):
        """档案里名字是空串/空白的条目跳过，不生成一张无名卡片。"""
        # 用 patch 而不是直接赋值 `pr._read_family`：直接赋值会在**类级别**改掉那个
        # 模块属性、泄漏到后续用例（本类各用例共用同一个模块对象），
        # 让一条本该红的测试因为上一条的残留而变绿——那种绿是最难查的。
        p = patch.object(pr, "_read_family", lambda: {"members": [{"name": "   "}, {"name": "我"}]})
        p.start()
        self.addCleanup(p.stop)
        self.write([meal(["钠:green"], members=["我"])])
        self.assertEqual([c["name"] for c in self.cards()], ["我"])

    # ── 归属筛选 ────────────────────────────────────────────────
    def test_only_own_meals_counted_old_key_falls_back(self):
        """新键 `members` 优先；只有旧键 `member` 时回落成单人数组；两个都没有 = 未归属。"""
        self.household(["小美", "我"])
        self.write([
            meal(["钠:green"], members=["小美"]),   # 新键
            meal(["钠:green"], member="小美"),      # 旧键回落
            meal(["钠:green"]),                    # 未归属 → 两个人都不算
        ])
        cards = {c["name"]: c for c in self.cards()}
        self.assertEqual(cards["小美"]["meals"], 2)
        self.assertEqual(cards["我"]["meals"], 0)

    def test_shared_meal_counts_for_everyone_at_once(self):
        """三人同桌 → 三张卡各 +1（这正是"一起吃饭"的定义）。"""
        self.household(["小美", "我", "阿婆"])
        self.write([meal(["钠:green"], members=["小美", "我", "阿婆"])])
        cards = {c["name"]: c for c in self.cards()}
        for who in ("小美", "我", "阿婆"):
            self.assertEqual(cards[who]["meals"], 1, who)

    # ── shared：只算多人同桌 ────────────────────────────────────
    def test_shared_counts_only_multi_person_meals(self):
        self.household(["小美", "我"])
        self.write([
            meal(["钠:green"], members=["小美"]),               # 独食
            meal(["钠:green"], members=["小美", "我"]),          # 同桌
            meal(["钠:green"], members=["我", "小美", "阿婆"]),  # 同桌
        ])
        cards = {c["name"]: c for c in self.cards()}
        self.assertEqual(cards["小美"]["meals"], 3)
        self.assertEqual(cards["小美"]["shared"], 2)

    # ── 灯计数与达标 ────────────────────────────────────────────
    def test_lights_are_composite_key_counts(self):
        """灯按复合键原样计数（与家庭版同构），前端用同一个 parseLights 拆。"""
        self.household(["我"])
        self.write([
            meal(["钠:green", "糖:green"], members=["我"]),
            meal(["钠:yellow"], members=["我"]),
        ])
        card = self.cards()[0]
        self.assertEqual(card["lights"], {"钠:green": 1, "糖:green": 1, "钠:yellow": 1})

    def test_on_target_uses_the_shared_verdict(self):
        """达标数走 `is_meal_on_target`：全绿才算，一条灯都没有计分母但不达标。"""
        self.household(["我"])
        self.write([
            meal(["钠:green", "糖:green"], members=["我"]),  # 达标
            meal(["钠:yellow"], members=["我"]),             # 未达标
            meal([], members=["我"]),                        # 无灯：计分母、不达标
        ])
        card = self.cards()[0]
        self.assertEqual(card["meals"], 3)
        self.assertEqual(card["on_target"], 1)

    # ── 窗口 ────────────────────────────────────────────────────
    def test_meals_outside_the_window_are_ignored(self):
        """窗口外的餐不进卡片 —— 用与周报页同一个 7 天口径。"""
        self.household(["我"])
        self.write([
            meal(["钠:green"], days_ago=1, members=["我"]),
            meal(["钠:green"], days_ago=30, members=["我"]),
        ])
        self.assertEqual(self.cards()[0]["meals"], 1)

    # ── 跨端点一致性（本文件存在的核心理由）────────────────────
    def test_card_numbers_equal_member_detail_numbers(self):
        """卡片说 N 餐，点进去的详情必须也是 N 餐；达标分子分母同样要对齐。

        两边走的是不同入口（这里传的是我筛好的 recent，详情自己 read_meals 再筛），
        所以这条不只是"同一个函数调了两次"——它验的是**两个入口筛出同一批餐**。
        """
        self.household(["小美", "我"])
        self.write([
            meal(["钠:green"], members=["小美", "我"]),
            meal(["钠:yellow"], members=["我"]),
            meal(["钠:green"], members=["小美"]),
            meal(["钠:green"]),                     # 未归属
            meal(["钠:green"], days_ago=30, members=["我"]),  # 窗口外
        ])
        cards = self.cards()
        self.assertTrue(cards)
        for card in cards:
            detail = rr.member_weekly_report(name=card["name"])
            self.assertTrue(detail["has_data"], card["name"])
            self.assertEqual(card["meals"], detail["meals"], f'{card["name"]} 餐数')
            prog = detail["progress"]
            self.assertEqual(card["on_target"], prog["numerator"], f'{card["name"]} 达标分子')
            self.assertEqual(card["meals"], prog["denominator"], f'{card["name"]} 分母')

    # ── 挂在真实返回体上 ────────────────────────────────────────
    def test_weekly_report_exposes_members(self):
        """`/reports/weekly` 真要带上这个字段（不是只有函数能用）。"""
        self.household(["我"])
        self.write([meal(["钠:green"], members=["我"])])
        r = rr.weekly_report()
        self.assertTrue(r["has_data"])
        self.assertIn("members", r)
        self.assertEqual([c["name"] for c in r["members"]], ["我"])

    def test_empty_week_has_no_members_key(self):
        """整周空 → 直接空态返回，没有成员卡片可言（不硬塞一个空数组进去）。"""
        self.household(["我"])
        self.write([])
        r = rr.weekly_report()
        self.assertFalse(r["has_data"])
        self.assertNotIn("members", r)

    def test_broken_ts_does_not_break_the_weekly_report(self):
        """一条坏 ts 不许让整个周报 500 —— 这正是本轮到 `_in_week` 的修复点。"""
        self.household(["我"])
        self.write([
            meal(["钠:green"], members=["我"]),
            {"ts": "not-a-date", "dish": "坏记录", "lights": [], "guardrails": 0},
        ])
        r = rr.weekly_report()
        self.assertTrue(r["has_data"])
        self.assertEqual(r["meals"], 1)


if __name__ == "__main__":
    unittest.main()
