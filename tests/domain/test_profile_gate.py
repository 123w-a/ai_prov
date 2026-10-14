"""充分性门控放宽：自述解析的子串别名匹配 + 否定排除。"""

import unittest
from unittest.mock import patch

from agent.graph import (
    _declared_covers,
    _hit_without_negation,
    _is_ambiguous_health_safety_question,
    _parse_declared_conditions,
    _self_declared_conditions,
    ask_user_node,
    profile_gate_node,
)
from langchain_core.messages import AIMessage, HumanMessage


class SelfDeclaredTest(unittest.TestCase):
    def test_natural_phrases_cover_conditions(self):
        cases = {
            "我是孕妇，孕18周，还有妊娠期高血压": {"孕期", "高血压"},
            "我孕18周想吃面": {"孕期"},
            "血压偏高能吃什么": {"高血压"},
            "我血压有点高，晚餐吃什么": {"高血压"},
            "血糖高的人喝什么汤": {"糖尿病"},
            "我血糖有点高，能吃什么": {"糖尿病"},
            "尿酸偏高怎么吃": {"痛风"},
            "肾脏不好要不要限蛋白": {"慢性肾脏病"},
            "我想减肥，晚餐怎么安排": {"肥胖"},
            "最近在减重，晚餐怎么安排": {"肥胖"},
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

    def test_profile_rendering_formats_are_parsed(self):
        declared = _parse_declared_conditions(
            "- 慢病情况：高血压、糖尿病（相关忌口按硬约束执行）\n"
            "- 当前目标：减重"
        )
        self.assertIn("高血压", declared)
        self.assertIn("糖尿病", declared)
        self.assertIn("减重", declared)

    def test_active_profile_condition_does_not_trigger_repeat_question(self):
        state = {
            "messages": [HumanMessage(content="今晚吃什么？")],
            "profile_ready": True,
            "profile_missing": [],
        }
        with patch("agent.graph._active_profile_conditions", return_value=["高血压"]):
            result = profile_gate_node(state)
        self.assertTrue(result["profile_ready"])
        self.assertEqual(result["profile_missing"], [])

    def test_explicit_blood_sugar_variant_requires_intensity(self):
        state = {
            "messages": [HumanMessage(content="我血糖有点高，晚餐吃什么？")],
            "profile_ready": True,
            "profile_missing": [],
        }
        with patch("agent.graph._active_profile_conditions", return_value=[]):
            result = profile_gate_node(state)
        self.assertFalse(result["profile_ready"])
        self.assertIn("控制强度", result["profile_missing"][0])

    def test_answer_after_previous_gate_question_does_not_repeat(self):
        state = {
            "messages": [
                HumanMessage(content="晚餐吃什么？"),
                AIMessage(content="想确认一下，你是否涉及糖尿病？"),
                HumanMessage(content="我血糖有点高"),
            ],
            "profile_ready": True,
            "profile_missing": [],
        }
        with patch("agent.graph._active_profile_conditions", return_value=[]):
            result = profile_gate_node(state)
        self.assertTrue(result["profile_ready"])
        self.assertEqual(result["profile_missing"], [])

    def test_pregnancy_week_number_matches(self):
        self.assertIn("孕期", _self_declared_conditions("我孕18周，想吃面条"))
        self.assertNotIn("孕期", _self_declared_conditions("我在备孕，想调理饮食"))

    def test_vague_discomfort_requires_clarification(self):
        result = profile_gate_node({
            "messages": [HumanMessage(content="我最近不舒服，吃啥？")],
        })
        self.assertFalse(result["profile_ready"])
        self.assertIn("具体症状", result["profile_missing"][0])

    def test_specific_discomfort_requires_severity(self):
        result = profile_gate_node({
            "messages": [HumanMessage(content="我胃不舒服，吃啥？")],
        })
        self.assertFalse(result["profile_ready"])
        self.assertIn("持续时间", result["profile_missing"][0])

    def test_ambiguous_safety_question_requires_food_target(self):
        state = {
            "messages": [HumanMessage(content="我妈能吃这个吗？")],
        }
        self.assertTrue(
            _is_ambiguous_health_safety_question(
                "我妈能吃这个吗？",
                state["messages"],
            )
        )
        result = profile_gate_node(state)
        self.assertFalse(result["profile_ready"])
        self.assertIn("具体食物或做法", result["profile_missing"][0])

    def test_common_colloquial_safety_questions_require_food_target(self):
        for text in (
            "我妈适合吃这道菜吗？",
            "我妈吃这个行吗？",
        ):
            with self.subTest(text=text):
                state = {"messages": [HumanMessage(content=text)]}
                result = profile_gate_node(state)
                self.assertFalse(result["profile_ready"])
                self.assertIn("具体食物或做法", result["profile_missing"][0])

    def test_safety_clarification_asks_for_the_missing_food_target(self):
        result = ask_user_node({
            "messages": [HumanMessage(content="我妈吃这个行吗？")],
            "profile_missing": ["具体食物或做法"],
        })
        self.assertIn("具体想判断哪种食物或做法", result["messages"][0].content)
        self.assertNotIn("饮食控制情况", result["messages"][0].content)

    def test_explicit_food_in_colloquial_safety_question_can_pass(self):
        state = {
            "messages": [
                HumanMessage(content="清蒸鲈鱼适合我妈吃吗？"),
            ],
        }
        result = profile_gate_node(state)
        self.assertTrue(result["profile_ready"])
        self.assertEqual(result["profile_missing"], [])

    def test_candidate_context_requires_candidate_selection(self):
        state = {
            "messages": [
                HumanMessage(content="今晚吃什么？"),
                AIMessage(content="1. 番茄炒蛋\n2. 蒜蓉蒸冬瓜"),
                HumanMessage(content="我妈能吃这个吗？"),
            ],
        }
        with patch("agent.graph._recent_candidates", return_value=["番茄炒蛋", "蒜蓉蒸冬瓜"]):
            result = profile_gate_node(state)
        self.assertFalse(result["profile_ready"])
        self.assertIn("候选序号", result["profile_missing"][0])

    def test_selected_card_context_allows_safety_followup(self):
        state = {
            "messages": [
                HumanMessage(content="做番茄炒蛋"),
                AIMessage(
                    content='{"recipes":[{"name":"番茄炒蛋"}]}'
                ),
                HumanMessage(content="我妈能吃这个吗？"),
            ],
        }
        result = profile_gate_node(state)
        self.assertTrue(result["profile_ready"])
        self.assertEqual(result["profile_missing"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
