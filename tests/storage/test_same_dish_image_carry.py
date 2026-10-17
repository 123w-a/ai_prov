"""同菜微调轮沿用上一张成品图：菜名归一化 + 图片检索 + answer 就地回填。

对应实测缺陷 sessions/user_d2cc49f12c rec7：用户对已交付的「醋溜土豆丝」追加
家庭控糖约束，Agent 重出一张同名卡片，但本轮不走配图链路，卡片上的成品图被清空。
"""

import json
import unittest
from unittest.mock import patch

from api.routes import chat_route
from storage import sessions as sessions_store


def _answer(recipes, image_url=None, ai=False, note=""):
    ans = {"recipes": recipes}
    if image_url:
        ans["image_url"] = image_url
        ans["image_ai_generated"] = ai
        if note:
            ans["image_note"] = note
    return json.dumps(ans, ensure_ascii=False)


class BaseDishNameTest(unittest.TestCase):
    def test_strips_version_suffix_and_parenthetical(self):
        base = sessions_store._base_dish_name
        self.assertEqual(base("蒜蓉蒸丝瓜·严格控制版"), "蒜蓉蒸丝瓜")
        self.assertEqual(base("清蒸鸡腿肉（去皮）"), "清蒸鸡腿肉")
        self.assertEqual(base("减盐减油版红烧肉"), "红烧肉")
        self.assertEqual(base("醋溜土豆丝"), "醋溜土豆丝")

    def test_empty_input(self):
        base = sessions_store._base_dish_name
        self.assertEqual(base(""), "")
        self.assertEqual(base(None), "")


class FindRecentRecipeImageTest(unittest.TestCase):
    def setUp(self):
        self.session = {
            "session_id": "s_carry",
            "title": "t",
            "created_at": "12:00",
            "messages": [
                {
                    "id": 1,
                    "user_text": "就第2个吧",
                    "answer": _answer(
                        [{"name": "蒜蓉蒸丝瓜"}],
                        image_url="https://img/x.png",
                        note="成品图",
                    ),
                },
                {
                    "id": 2,
                    "user_text": "要严格控制",
                    "answer": _answer([{"name": "蒜蓉蒸丝瓜·严格控制版"}]),
                },
            ],
        }

    def test_matches_previous_card_by_normalized_name(self):
        with patch.object(sessions_store, "_read_session", return_value=self.session):
            got = sessions_store.find_recent_recipe_image(
                "s_carry", "蒜蓉蒸丝瓜·严格控制版"
            )
        self.assertEqual(got["url"], "https://img/x.png")
        self.assertEqual(got["note"], "成品图")
        self.assertFalse(got["ai"])

    def test_other_dish_does_not_reuse(self):
        with patch.object(sessions_store, "_read_session", return_value=self.session):
            self.assertIsNone(
                sessions_store.find_recent_recipe_image("s_carry", "青椒肉丝")
            )

    def test_record_without_image_is_skipped(self):
        session = {
            "session_id": "s_noop",
            "messages": [
                {
                    "id": 1,
                    "user_text": "先看看",
                    "answer": _answer([{"name": "醋溜土豆丝"}]),
                }
            ],
        }
        with patch.object(sessions_store, "_read_session", return_value=session):
            self.assertIsNone(
                sessions_store.find_recent_recipe_image("s_noop", "醋溜土豆丝")
            )

    def test_missing_session_returns_none(self):
        with patch.object(sessions_store, "_read_session", return_value=None):
            self.assertIsNone(
                sessions_store.find_recent_recipe_image("s_none", "醋溜土豆丝")
            )


class CarryOverSameDishImageTest(unittest.TestCase):
    def _carry(self, answer, found):
        with patch(
            "storage.sessions.find_recent_recipe_image", return_value=found
        ) as mocked:
            ok = chat_route._carry_over_same_dish_image(answer, "s1")
        return ok, mocked

    def test_single_same_dish_reuses_previous_image(self):
        answer = {"recipes": [{"name": "醋溜土豆丝·少油版"}]}
        ok, _ = self._carry(
            answer,
            {"url": "https://img/tudou.png", "ai": False, "note": "成品图"},
        )
        self.assertTrue(ok)
        self.assertEqual(answer["recipes"][0]["image_url"], "https://img/tudou.png")
        self.assertEqual(answer["image_url"], "https://img/tudou.png")
        self.assertEqual(answer["recipes"][0]["image_note"], "成品图")
        self.assertTrue(answer["image_requested"])

    def test_multi_dish_card_is_left_alone(self):
        answer = {"recipes": [{"name": "醋溜土豆丝"}, {"name": "蒜蓉西兰花"}]}
        ok, mocked = self._carry(
            answer, {"url": "https://img/x.png", "ai": False, "note": ""}
        )
        self.assertFalse(ok)
        mocked.assert_not_called()
        self.assertNotIn("image_requested", answer)

    def test_no_previous_image_keeps_answer_untouched(self):
        answer = {"recipes": [{"name": "醋溜土豆丝"}]}
        ok, _ = self._carry(answer, None)
        self.assertFalse(ok)
        self.assertNotIn("image_url", answer)
        self.assertNotIn("image_requested", answer)

    def test_empty_answer_is_safe(self):
        ok, _ = self._carry({}, {"url": "https://img/x.png", "ai": False, "note": ""})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
