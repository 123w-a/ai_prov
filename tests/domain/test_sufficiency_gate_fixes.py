# -*- coding: utf-8 -*-
"""充分性门控：成员绑定 / 自然回答去重 / 强度标记收紧 —— 回归固化。

三处修复（2026-10-14，统一门控行为）：
1. 成员错配：点名具体成员（「我爸高血压」）时，不再拿**激活成员**的档案 restricts
   顶掉追问（实测：激活成员填了医嘱后，问「我爸高血压」完全不再追问）。
2. 自然回答去重：上一轮 AI 若是本系统追问话术 → 本轮无条件放行，不依赖
   `state["profile_missing"]` 跨轮传递（实测：答「就是少吃点咸的」会被原样再问）。
3. 强度标记收紧：「轻度 / 轻度偏高 / 偏高一点 / 临界 / 刚查出来」不再当作
   「已说清控制强度」（这些恰恰是"还不知道严到什么程度"，该问）。
"""

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import (
    _MISSING_INTENSITY,
    _intensity_sufficient,
    _last_ai_asked,
    _self_declared_intensity,
    profile_gate_node,
)

_ASK = (
    "想确认一下：医生有没有给你具体的忌口要求（比如严格控盐、限嘌呤），"
    "还是平时注意一下就行？"
)


def _gate(text, *, restricts=None, asked=None, prior_ai=None):
    """跑 profile_gate_node（patch 掉档案读取，避免依赖真实 profile.json）。"""
    messages = [HumanMessage(content=text)]
    if prior_ai is not None:
        messages = [
            HumanMessage(content="（上一轮用户消息）"),
            AIMessage(content=prior_ai),
            HumanMessage(content=text),
        ]
    with patch(
        "agent.graph._active_profile_conditions", return_value=[]
    ), patch(
        "agent.graph._active_profile_restricts", return_value=list(restricts or [])
    ):
        return profile_gate_node(
            {"messages": messages, "profile_missing": list(asked or [])}
        )


class MemberBindingTest(unittest.TestCase):
    """缺陷1：档案 restricts 属于激活成员，点名其他成员时不得顶掉追问。"""

    def test_named_other_member_not_silenced_by_active_profile(self):
        with patch("agent.graph._active_profile_restricts", return_value=["低盐饮食"]):
            self.assertFalse(_intensity_sufficient("我爸高血压，中午吃什么"))

    def test_self_reference_still_reads_profile(self):
        with patch("agent.graph._active_profile_restricts", return_value=["低盐饮食"]):
            self.assertTrue(_intensity_sufficient("我有高血压，中午吃什么"))

    def test_named_member_with_declared_intensity_passes(self):
        with patch("agent.graph._active_profile_restricts", return_value=["低盐饮食"]):
            self.assertTrue(_intensity_sufficient("我爸高血压，医生让严格控盐"))

    def test_gate_still_asks_even_when_active_profile_has_orders(self):
        out = _gate("我爸高血压，中午吃什么", restricts=["低盐饮食"])
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_gate_self_declared_intensity_passes(self):
        out = _gate("我有高血压，医生让严格控盐，中午吃什么")
        self.assertTrue(out["profile_ready"])

    def test_extended_member_markers(self):
        """父亲/母亲/儿子/女儿/妻子/丈夫/岳父/婆婆/家里人 等称呼同样算「点名了成员」。

        回归：人称表覆盖不全时，这些说法会回落去读激活成员档案 → 成员错配缺陷复现。
        """
        with patch("agent.graph._active_profile_restricts", return_value=["低盐饮食"]):
            for text in (
                "我父亲高血压", "我母亲糖尿病", "给我儿子做饭", "我女儿不吃辣",
                "我妻子高血压", "我丈夫血压高", "我岳父痛风", "我婆婆血糖高",
                "家里人都有高血压",
            ):
                with self.subTest(text=text):
                    self.assertFalse(_intensity_sufficient(text))


class NaturalAnswerDedupTest(unittest.TestCase):
    """缺陷2：上一轮问过 → 本轮是回答 → 不再追问（不依赖 profile_missing）。"""

    def test_natural_answer_with_condition_not_reasked(self):
        out = _gate("我有高血压，就是少吃点咸的", prior_ai=_ASK, asked=[])
        self.assertTrue(out["profile_ready"])

    def test_dedup_survives_missing_profile_missing(self):
        # state["profile_missing"] 为空（跨轮丢失）时也要去重
        out = _gate("就是少吃点咸的", prior_ai=_ASK, asked=[])
        self.assertTrue(out["profile_ready"])

    def test_new_meal_request_after_ask_is_not_treated_as_answer(self):
        out = _gate("我有高血压，中午吃什么", prior_ai=_ASK, asked=[])
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_new_information_question_after_ask_is_not_treated_as_answer(self):
        out = _gate("高血压是什么病？", prior_ai=_ASK, asked=[])
        # 知识问答不属于饮食求餐，门控可以放行；这里守住的是它不能被
        # 当成追问回答后再回溯成上一轮求餐。
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_recipe_closing_question_does_not_count_as_ask(self):
        self.assertFalse(_last_ai_asked([AIMessage(content="要不要我帮你配图？")]))
        self.assertFalse(
            _last_ai_asked([AIMessage(content="## 宫保鸡丁\n**食材**：鸡胸肉")])
        )

    def test_ask_turn_lead_recognized(self):
        self.assertTrue(_last_ai_asked([AIMessage(content=_ASK)]))
        self.assertTrue(
            _last_ai_asked(
                [AIMessage(content="请补充具体症状、持续了多久，以及是否有明显或严重表现？")]
            )
        )


class IntensityMarkerNegationTest(unittest.TestCase):
    """缺陷3：「还没说清」的词不得当作已说清强度。"""

    def test_vague_markers_not_sufficient(self):
        for text in (
            "刚查出来高血压，中午吃什么",
            "血压偏高一点，吃什么",
            "临界高血压，吃什么",
            "我有高血压，轻度的那种，中午吃什么",
        ):
            with self.subTest(text=text):
                self.assertFalse(_self_declared_intensity(text))

    def test_real_intensity_markers_still_sufficient(self):
        for text in (
            "我高血压，医生让严格控盐",
            "我高血压，平时注意一点就行",
            "遵医嘱低盐饮食",
            "高血压控制稳定",
        ):
            with self.subTest(text=text):
                self.assertTrue(_self_declared_intensity(text))

    def test_gate_asks_for_vague_marker(self):
        out = _gate("刚查出来高血压，中午吃什么")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])


class OpenGateMatrixRegressionTest(unittest.TestCase):
    """用户实测的统一门控行为矩阵：修复只补缺陷，不改变既有形态。"""

    def test_matrix(self):
        cases = [
            ("今晚一家三口吃什么？爸爸高血压，妈妈痛风，孩子花生过敏。", False),
            ("我爸高血压，中午吃什么？", False),
            ("我有高血压，想吃红烧肉", False),
            ("我在备孕，吃什么好", False),
            ("中午吃什么", True),
        ]
        for text, expect_ready in cases:
            with self.subTest(text=text):
                out = _gate(text)
                self.assertEqual(out["profile_ready"], expect_ready)


if __name__ == "__main__":
    unittest.main(verbosity=2)
