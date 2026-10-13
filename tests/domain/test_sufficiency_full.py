# -*- coding: utf-8 -*-
"""充分性增强档（CHEF_SUFFICIENCY_FULL）行为固化。［留档副本，正式位置 tests/domain/］

- 默认档 = 基线：用户自述病种即放行，不重复追问（末尾有回归守门）。
- 增强档 = 补齐「控制强度」与「备孕」两个维度。

开关通过 patch 模块属性切换，不依赖环境变量，避免污染其他用例。
"""

import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from agent.graph import (
    _MISSING_INTENSITY,
    _MISSING_PREGNANCY,
    _self_declared_intensity,
    ask_user_node,
    profile_gate_node,
)


def _gate(text, active=None, restricts=None, full=True):
    with patch("agent.graph.SUFFICIENCY_FULL", full), patch(
        "agent.graph._active_profile_conditions", return_value=list(active or [])
    ), patch(
        "agent.graph._active_profile_restricts", return_value=list(restricts or [])
    ):
        return profile_gate_node({"messages": [HumanMessage(content=text)]})


class IntensityMarkerTest(unittest.TestCase):
    def test_declared_intensity_markers(self):
        for text in (
            "医生让我严格控盐",
            "遵医嘱少盐少油",
            "没什么大事，就平时注意点",
            "在吃药，控制得还行",
            "刚查出来，还比较轻度",
        ):
            self.assertTrue(_self_declared_intensity(text), text)

    def test_undeclared_intensity(self):
        for text in ("我血压有点高", "我有糖尿病", "中午吃什么"):
            self.assertFalse(_self_declared_intensity(text), text)


class EnhancedGateTest(unittest.TestCase):
    def test_named_condition_without_intensity_asks(self):
        """用户材料里的原例：说了病、没说控制到什么程度。"""
        out = _gate("我爸高血压，中午吃什么？")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_second_condition_variant_asks(self):
        out = _gate("我有高血压，想吃红烧肉")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_intensity_declared_passes(self):
        out = _gate("我有高血压，医生让严格控盐，中午吃什么")
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_profile_restricts_prevents_repeat_question(self):
        """档案里已写医嘱硬限制 → 不再追问（防止一问再问）。"""
        out = _gate("我有高血压，中午吃什么", restricts=["严格控盐"])
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_profile_only_condition_does_not_ask_intensity(self):
        """病种只由档案带入（本轮没提），不打扰用户。"""
        out = _gate("今晚吃什么？", active=["高血压"])
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_plain_request_still_passes(self):
        """最高优先回归项：普通点菜绝不能被变严。"""
        out = _gate("中午吃什么")
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_pregnancy_planning_asks(self):
        """备孕≠孕期：补上原本走不到的「宁可追问」分支。"""
        out = _gate("我在备孕，吃什么好")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_PREGNANCY])

    def test_unknown_condition_still_asks_condition_first(self):
        """病种本身没说清时，优先问病种，不问强度。"""
        out = _gate("我想吃降血糖的菜")
        self.assertFalse(out["profile_ready"])
        self.assertNotEqual(out["profile_missing"], [_MISSING_INTENSITY])


class FixedWordingTest(unittest.TestCase):
    def test_intensity_question_is_fixed_wording(self):
        result = ask_user_node(
            {
                "messages": [HumanMessage(content="我爸高血压，中午吃什么")],
                "profile_missing": [_MISSING_INTENSITY],
            }
        )
        content = result["messages"][0].content
        self.assertIn("忌口要求", content)
        self.assertNotIn("饮食控制情况", content)

    def test_pregnancy_question_is_fixed_wording(self):
        result = ask_user_node(
            {
                "messages": [HumanMessage(content="我在备孕")],
                "profile_missing": [_MISSING_PREGNANCY],
            }
        )
        self.assertIn("孕几周", result["messages"][0].content)


class DefaultOffKeepsBaselineTest(unittest.TestCase):
    """开关关闭时必须与既有基线一致——这是不许被顺手改掉的那条线。"""

    def test_default_off_does_not_ask_intensity(self):
        out = _gate("我有高血压，中午吃什么", full=False)
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])

    def test_default_off_does_not_ask_pregnancy(self):
        out = _gate("我在备孕，吃什么好", full=False)
        self.assertTrue(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
