# -*- coding: utf-8 -*-
"""充分性门控统一行为回归。"""

import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from agent.graph import (
    _MISSING_ALLERGEN_SEVERITY,
    _MISSING_INTENSITY,
    _MISSING_POPULATION,
    _MISSING_PREGNANCY,
    _MISSING_SYMPTOM_SEVERITY,
    _self_declared_intensity,
    ask_user_node,
    profile_gate_node,
)


def _gate(text, active=None, restricts=None):
    with patch(
        "agent.graph._active_profile_conditions", return_value=list(active or [])
    ), patch(
        "agent.graph._active_profile_restricts", return_value=list(restricts or [])
    ):
        return profile_gate_node({"messages": [HumanMessage(content=text)]})


def _gate2(text, active=None, restricts=None, allergens=None, members=None,
           asked=None):
    """带档案/成员/上一轮追问键的完整门控调用。"""
    with patch(
        "agent.graph._active_profile_conditions", return_value=list(active or [])
    ), patch(
        "agent.graph._active_profile_restricts", return_value=list(restricts or [])
    ), patch(
        "agent.graph._active_profile_allergens", return_value=set(allergens or [])
    ), patch(
        "agent.graph._family_members",
        return_value=[{"name": n, "profile": {}} for n in (members or [])],
    ):
        return profile_gate_node(
            {
                "messages": [HumanMessage(content=text)],
                "profile_missing": list(asked or []),
            }
        )


class IntensityMarkerTest(unittest.TestCase):
    def test_declared_intensity_markers(self):
        for text in (
            "医生让我严格控盐",
            "遵医嘱少盐少油",
            "没什么大事，就平时注意点",
            "在吃药，控制得还行",
        ):
            self.assertTrue(_self_declared_intensity(text), text)

    def test_undeclared_intensity(self):
        for text in ("我血压有点高", "我有糖尿病", "中午吃什么"):
            self.assertFalse(_self_declared_intensity(text), text)

    def test_vague_diagnosis_not_declared_intensity(self):
        """「刚查出来 / 轻度 / 偏高一点 / 临界」= 还不知道严到什么程度，不算已说清。

        2026-10-14 判反修复：原用例把「刚查出来，还比较轻度」归在"已说清"组，
        但它恰恰是"还没定级"的语境 —— 这里应该问，不该放过。
        """
        for text in (
            "刚查出来，还比较轻度",
            "刚查出来高血压",
            "血压偏高一点",
            "临界高血压",
        ):
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


class SymptomSeverityTest(unittest.TestCase):
    """B2：说了「哪儿不舒服」但没说「到什么程度」。"""

    def test_specific_symptom_without_severity_asks(self):
        out = _gate2("最近胃不好，吃什么好")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_SYMPTOM_SEVERITY])

    def test_severity_declared_passes(self):
        for text in ("偶尔胃疼，吃什么好", "胃疼好几天了，吃什么好", "有点头晕，吃什么好"):
            with self.subTest(text=text):
                out = _gate2(text)
                self.assertTrue(out["profile_ready"])

    def test_plain_request_not_asked(self):
        out = _gate2("推荐几道家常菜")
        self.assertTrue(out["profile_ready"])


class AllergenSeverityTest(unittest.TestCase):
    """B2：过敏原强度——三重信号才触发（过敏词 + 说到过敏原 + 档案无记录）。"""

    def test_new_allergen_without_severity_asks(self):
        out = _gate2("我对花生过敏，中午吃什么", allergens=[])
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_ALLERGEN_SEVERITY])

    def test_severity_declared_passes(self):
        out = _gate2("我对花生过敏，吃一点就起疹，中午吃什么", allergens=[])
        self.assertTrue(out["profile_ready"])

    def test_profile_allergen_silences_question(self):
        """已在档的过敏原按 L3 全禁处理，不再追问。"""
        out = _gate2("我对花生过敏，中午吃什么", allergens=["花生"])
        self.assertTrue(out["profile_ready"])

    def test_negated_allergy_not_treated_as_declaration(self):
        for text in ("我不过敏，随便推荐", "我没有过敏"):
            with self.subTest(text=text):
                out = _gate2(text, allergens=[])
                self.assertTrue(out["profile_ready"])


class PopulationTest(unittest.TestCase):
    """B3：只对不在档案里的对象追问，在档成员一律不重复索取。"""

    def test_unknown_member_without_age_asks(self):
        out = _gate2("给我奶奶做顿饭", members=["孩子", "爸爸", "妈妈"])
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_POPULATION])

    def test_age_declared_passes(self):
        out = _gate2("给我奶奶做顿饭，她 80 岁了", members=["孩子"])
        self.assertTrue(out["profile_ready"])

    def test_profiled_member_not_asked(self):
        for text in ("给孩子做顿饭", "给爸爸做顿饭", "给妈妈做顿饭"):
            with self.subTest(text=text):
                out = _gate2(text, members=["孩子", "爸爸", "妈妈"])
                self.assertTrue(out["profile_ready"])


class PregnancyNestedBranchTest(unittest.TestCase):
    """批 1 的备孕分支嵌在 if not conditions 里：同时说病种就走不到（已修）。"""

    def test_pregnancy_with_condition_still_asks(self):
        out = _gate2("我在备孕，尿酸偏高，吃什么")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_PREGNANCY])


class DedupTest(unittest.TestCase):
    """追问去重：state['profile_missing'] 即上一轮追问键，同键不连问第二次。"""

    def test_same_key_not_asked_twice(self):
        first = _gate2("我爸高血压，中午吃什么")
        self.assertFalse(first["profile_ready"])
        second = _gate2("我爸高血压，中午吃什么", asked=first["profile_missing"])
        self.assertTrue(second["profile_ready"])

    def test_four_rounds_ask_only_once(self):
        asked, asked_times = [], 0
        for _ in range(4):
            out = _gate2("我有高血压，中午吃什么", asked=asked)
            if not out["profile_ready"]:
                asked_times += 1
                asked = out["profile_missing"]
        self.assertEqual(asked_times, 1)


class ChitchatGuardTest(unittest.TestCase):
    """只在「确实在求餐」时补问；闲聊提到病名不打扰。"""

    def test_chitchat_mentioning_condition_not_asked(self):
        for text in ("我同事说他高血压挺久了", "我有高血压"):
            with self.subTest(text=text):
                out = _gate2(text)
                self.assertTrue(out["profile_ready"])


class CalibrationRegressionTest(unittest.TestCase):
    """离线校准（.tmpdir/sufficiency_calib）里暴露并已修的 5 个案例，固化防回退。"""

    def test_meal_request_variant_still_asks(self):
        """「吃点啥好」也是求餐，不能因为词表只收「吃啥」就漏问。"""
        out = _gate2("尿酸高，吃点啥好")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_third_party_talk_not_asked(self):
        """转述同事/邻居的病名不是这顿饭的约束，不补问。"""
        for text in ("我同事说他高血压挺久了，午饭吃啥", "邻居血压高，晚饭推荐点什么"):
            with self.subTest(text=text):
                out = _gate2(text)
                self.assertTrue(out["profile_ready"])

    def test_self_stated_goal_counts_as_intensity(self):
        """「我想减肥」已是用户给的控制口径，不该再问医嘱严格程度。"""
        out = _gate2("我想减肥，晚饭怎么安排")
        self.assertTrue(out["profile_ready"])

    def test_infant_stage_counts_as_population_detail(self):
        """说「婴儿/辅食」已定位人群阶段，不再问多大年纪。"""
        out = _gate2("家里有婴儿，辅食吃什么")
        self.assertTrue(out["profile_ready"])

    def test_colloquial_symptom_variant_asks(self):
        out = _gate2("肚子胀，晚上吃什么")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_SYMPTOM_SEVERITY])

    def test_new_allergen_with_profiled_allergen_still_asks(self):
        """档案里已记花生，本轮改说对虾过敏 —— 新信息仍要问一次。"""
        out = _gate2("我对虾过敏，中午吃什么", allergens=["花生"])
        self.assertFalse(out["profile_ready"])
        self.assertEqual(out["profile_missing"], [_MISSING_ALLERGEN_SEVERITY])

    def test_same_allergen_on_file_not_asked(self):
        out = _gate2("我对花生过敏，中午吃什么", allergens=["花生"])
        self.assertTrue(out["profile_ready"])


class NightlyEdgeCaseTest(unittest.TestCase):
    """扩语料加压后抓到的边界案例。"""

    def test_pregnancy_not_treated_as_chronic_intensity(self):
        """孕期走人群口径，不该问「医生有没有要求严格控盐」。"""
        out = _gate2("我媳妇怀孕了，吃什么好")
        self.assertTrue(out["profile_ready"])

    def test_negated_discomfort_not_asked(self):
        """「我没有不舒服」是明确否认，不该再问症状。"""
        out = _gate2("我没有不舒服，就想吃点清淡的")
        self.assertTrue(out["profile_ready"])

    def test_multi_condition_asks_once(self):
        out = _gate2("我有高血压和糖尿病，中午吃什么")
        self.assertFalse(out["profile_ready"])
        self.assertEqual(len(out["profile_missing"]), 1)

    def test_condition_outranks_symptom(self):
        """同时有病种和症状时，先问病种控制强度（安全优先级更高）。"""
        out = _gate2("我爸高血压，他最近还老是头晕，晚饭做什么")
        self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])

    def test_negated_allergy_with_symptom_asks_symptom(self):
        out = _gate2("我不过敏，最近胃疼，吃什么")
        self.assertEqual(out["profile_missing"], [_MISSING_SYMPTOM_SEVERITY])


if __name__ == "__main__":
    unittest.main(verbosity=2)
