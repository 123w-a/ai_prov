"""充分性门控放宽：自述解析的子串别名匹配 + 否定排除。"""

import unittest

from agent_graph import (
    _declared_covers,
    _hit_without_negation,
    _self_declared_conditions,
)


class SelfDeclaredTest(unittest.TestCase):
    def test_natural_phrases_cover_conditions(self):
        cases = {
            "我是孕妇，孕18周，还有妊娠期高血压": {"孕期", "高血压"},
            "我孕18周想吃面": {"孕期"},
            "血压偏高能吃什么": {"高血压"},
            "血糖高的人喝什么汤": {"糖尿病"},
            "尿酸偏高怎么吃": {"痛风"},
            "肾脏不好要不要限蛋白": {"慢性肾脏病"},
            "我想减肥，晚餐怎么安排": {"肥胖"},
            "来点家常菜": set(),
        }
        for text, expected in cases.items():
            self.assertEqual(
                _self_declared_conditions(text), expected, text
            )

    def test_negation_not_treated_as_declaration(self):
        self.assertNotIn("高血压", _self_declared_conditions("我没有高血压"))
        self.assertNotIn("糖尿病", _self_declared_conditions("无糖尿病史"))
        self.assertIn("高血压", _self_declared_conditions("我有高血压"))

    def test_hit_without_negation_helper(self):
        self.assertTrue(_hit_without_negation("我有高血压", "高血压"))
        self.assertFalse(_hit_without_negation("我没有高血压", "高血压"))
        self.assertFalse(_hit_without_negation("无异嘌呤问题", "嘌呤"))

    def test_declared_covers_fuzzy(self):
        self.assertTrue(_declared_covers("高血压", {"妊娠期高血压"}))
        self.assertTrue(_declared_covers("孕期", {"怀孕期"}))
        self.assertFalse(_declared_covers("高血压", {"糖尿病"}))

    def test_pregnancy_week_number_matches(self):
        self.assertIn("孕期", _self_declared_conditions("我孕18周，想吃面条"))
        self.assertNotIn("孕期", _self_declared_conditions("我在备孕，想调理饮食"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
