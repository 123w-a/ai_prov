# tests/test_allergen_guardrail.py
# 过敏原硬护栏回归：规则优先级、图节点阻断、结构化复核、流式转发。

import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

import allergen_rules
import agent_graph
import main
from agent_schemas import ChefAnswer, Recipe, Seasoning


class TestAllergenRuleTable(unittest.TestCase):
    def test_eight_mandatory_categories(self):
        self.assertEqual(len(allergen_rules.ALLERGEN_RULES), 8)
        for code in (
            "gluten", "crustacean", "fish", "egg",
            "peanut", "soy", "milk", "nuts",
        ):
            self.assertIn(code, allergen_rules.ALLERGEN_RULES)


class TestHardBlocks(unittest.TestCase):
    def test_ten_required_allergen_blocks(self):
        cases = [
            ("花生过敏", "宫保鸡丁，放花生米", ["peanut"]),
            ("虾过敏", "虾仁炒蛋", ["crustacean"]),
            ("乳过敏", "奶油意面", ["milk"]),
            ("麸质过敏", "酱油炒饭", ["gluten"]),
            ("蛋过敏", "蛋黄酱沙拉", ["egg"]),
            ("大豆过敏", "麻婆豆腐", ["soy"]),
            ("坚果过敏", "腰果鸡丁", ["nuts"]),
            ("鱼过敏", "鱼露拌菜", ["fish"]),
            ("蟹过敏", "蟹黄豆腐", ["crustacean"]),
            ("麸质过敏", "蚝油生菜", ["gluten"]),
        ]
        for label, text, allergens in cases:
            with self.subTest(label=label):
                violations = allergen_rules.audit_allergens(text, allergens)
                self.assertTrue(violations, f"{label}未拦住：{text}")
                self.assertTrue(all(v["dimension"] == "allergen" for v in violations))


class TestChronicRegression(unittest.TestCase):
    def test_ten_existing_nutrition_rules_still_fire(self):
        cases = [
            (["高血压"], "咸菜炒肉"),
            (["高血压"], "酱油红烧肉"),
            (["糖尿病"], "蜂蜜甜饮"),
            (["糖尿病"], "肥肉盖饭"),
            (["高脂血症"], "黄油煎牛排"),
            (["痛风"], "老火汤炖猪肝"),
            (["慢性肾脏病"], "老火汤配腊肉"),
            (["肥胖"], "白砂糖30g做甜品"),
            (["孕期"], "生鱼片配酒"),
            ([], "老火汤炖猪肝"),
        ]
        for conditions, text in cases:
            with self.subTest(conditions=conditions, text=text):
                result = allergen_rules.audit_allergens(text, [])
                self.assertEqual(result, [])
                if conditions:
                    self.assertTrue(agent_graph.audit(text, conditions))
                else:
                    self.assertEqual(agent_graph.audit(text, conditions), [])


class TestNoFalsePositive(unittest.TestCase):
    def test_ten_unrelated_dishes_stay_clean(self):
        cases = [
            ("番茄炒蛋", ["fish"]),
            ("青椒肉丝", ["egg"]),
            ("清炒时蔬", ["milk"]),
            ("清蒸鸡腿", ["crustacean"]),
            ("蒜蓉青菜", ["peanut"]),
            ("土豆炖牛肉", ["soy"]),
            ("冬瓜排骨汤", ["nuts"]),
            ("香菇滑鸡", ["fish"]),
            ("清炒西兰花", ["milk"]),
            ("蒸南瓜", ["gluten"]),
        ]
        for text, allergens in cases:
            with self.subTest(text=text, allergens=allergens):
                self.assertEqual(allergen_rules.audit_allergens(text, allergens), [])


class TestMatchPriority(unittest.TestCase):
    def test_exclusions_prevent_substring_false_positives(self):
        cases = [
            ("鱼香肉丝", ["fish"]),
            ("优质蛋白质", ["egg"]),
            ("蟹柳", ["crustacean"]),
            ("蟹肉棒", ["crustacean"]),
            ("乳胶手套", ["milk"]),
            ("核桃木餐桌", ["nuts"]),
        ]
        for text, allergens in cases:
            with self.subTest(text=text):
                self.assertEqual(allergen_rules.audit_allergens(text, allergens), [])

    def test_exclusion_does_not_hide_real_allergen_later_in_text(self):
        result = allergen_rules.audit_allergens(
            "鱼香肉丝配清蒸鲈鱼",
            ["fish"],
        )
        self.assertTrue(result)
        self.assertEqual(result[0]["keyword"], "鲈鱼")

    def test_soy_sauce_hits_both_soy_and_gluten(self):
        result = allergen_rules.audit_allergens("生抽炒饭", ["soy", "gluten"])
        conditions = {item["condition"] for item in result}
        self.assertIn("过敏原:大豆及其制品", conditions)
        self.assertIn("过敏原:含麸质的谷物及其制品", conditions)

    def test_maybe_hidden_is_notice_only(self):
        hard = allergen_rules.audit_allergens("XO酱炒饭", ["crustacean"])
        notices = allergen_rules.audit_allergen_advisories("XO酱炒饭", ["crustacean"])
        self.assertEqual(hard, [])
        self.assertTrue(notices)
        self.assertEqual(notices[0]["dimension"], "allergen_notice")


class TestNormalization(unittest.TestCase):
    def test_common_upper_terms_expand(self):
        self.assertEqual(
            allergen_rules.normalize_allergens("海鲜"),
            ["crustacean", "fish"],
        )
        self.assertEqual(allergen_rules.normalize_allergens("奶制品"), ["milk"])
        self.assertEqual(allergen_rules.normalize_allergens("豆制品"), ["soy"])
        self.assertEqual(allergen_rules.normalize_allergens("gluten"), ["gluten"])
        self.assertEqual(
            allergen_rules.normalize_allergens("乳糖不耐"),
            ["milk"],
        )
        self.assertEqual(
            allergen_rules.normalize_allergens("乳过敏"),
            ["milk"],
        )

    def test_upper_term_does_not_hide_other_allergens(self):
        self.assertCountEqual(
            allergen_rules.normalize_allergens("海鲜、花生、乳制品"),
            ["crustacean", "fish", "peanut", "milk"],
        )

    def test_unresolved_is_logged_not_silent(self):
        original_file = allergen_rules.__file__
        allergen_rules._UNRESOLVED_ALREADY_LOGGED.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            allergen_rules.__file__ = str(Path(temp_dir) / "allergen_rules.py")
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    result = allergen_rules.normalize_allergen("完全未知的忌口")
                log_path = Path(temp_dir) / "data" / "allergen_unresolved.log"
                self.assertIsNone(result)
                self.assertTrue(caught)
                self.assertIn("完全未知的忌口", log_path.read_text(encoding="utf-8"))
            finally:
                allergen_rules.__file__ = original_file
                allergen_rules._UNRESOLVED_ALREADY_LOGGED.clear()

    def test_safe_suggestions_are_audited(self):
        dishes = allergen_rules.suggest_safe_dishes(["crustacean"], k=3)
        self.assertEqual(len(dishes), 3)
        for name in dishes:
            self.assertEqual(allergen_rules.audit_allergens(name, ["crustacean"]), [])

    def test_safe_suggestions_honor_optional_allergens(self):
        candidates = [
            ("麻酱拌面", "面条 芝麻酱"),
            ("清炒青菜", "青菜 盐"),
        ]
        with patch.object(allergen_rules, "_SAFE_DISH_CANDIDATES", candidates):
            dishes = allergen_rules.suggest_safe_dishes(
                ["芝麻过敏"],
                k=3,
                use_optional=True,
            )
        self.assertEqual(dishes, ["清炒青菜"])

    def test_describe_is_traceable(self):
        violations = allergen_rules.audit_allergens("虾仁炒蛋", ["crustacean"])
        text = allergen_rules.describe_allergens(violations)
        self.assertIn("甲壳纲", text)
        self.assertIn("来源", text)


class TestVerifyGraph(unittest.TestCase):
    def _state(self, ai_text, attempts=0, **extra):
        return {
            "messages": [HumanMessage(content="今晚吃什么"), AIMessage(content=ai_text)],
            "verify_attempts": attempts,
            **extra,
        }

    def test_allergen_hit_retries_under_limit(self):
        with patch("agent_graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.verify_answer_node(self._state("推荐油焖大虾"))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("过敏原是绝对硬约束", result["messages"][0].content)

    def test_allergen_exhaustion_blocks_instead_of_degrading(self):
        with patch("agent_graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.verify_answer_node(
                self._state("推荐油焖大虾", attempts=agent_graph.MAX_VERIFY)
            )
        self.assertEqual(result["verify_status"], "blocked")
        self.assertFalse(result.get("verify_warning"))

    def test_tool_budget_does_not_degrade_allergen(self):
        with patch("agent_graph._allergens_for_audit", return_value=["花生"]):
            result = agent_graph.verify_answer_node(
                self._state("花生拌面", tool_budget_exhausted=True)
            )
        self.assertEqual(result["verify_status"], "blocked")

    def test_chronic_exhaustion_keeps_degraded(self):
        with (
            patch("agent_graph._allergens_for_audit", return_value=[]),
            patch("agent_graph._merged_conditions", return_value=["痛风"]),
        ):
            result = agent_graph.verify_answer_node(
                self._state("老火汤炖猪肝", attempts=agent_graph.MAX_VERIFY)
            )
        self.assertEqual(result["verify_status"], "degraded")
        self.assertTrue(result["verify_warning"].startswith("⚠️"))

    def test_verify_route_keeps_blocked_even_when_budget_exhausted(self):
        status = agent_graph.verify_route({
            "verify_status": "blocked",
            "tool_budget_exhausted": True,
            "messages": [],
        })
        self.assertEqual(status, "blocked")

    def test_safety_node_and_route_are_registered(self):
        nodes = set(agent_graph.agent.get_graph().nodes.keys())
        self.assertIn("allergen_block", nodes)

    def test_safety_node_returns_plain_safe_tip(self):
        with patch("agent_graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.allergen_block_node({
                "messages": [],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        self.assertEqual(result["verify_status"], "blocked")
        self.assertIsInstance(result["messages"][0], AIMessage)
        self.assertIn("不能推荐", result["messages"][0].content)

    def test_safety_node_emits_structured_payload_with_blocked_guardrail(self):
        """拦截必须"看得见"：blocked 也要产出结构化 payload + 已拦截护栏。

        回归背景——此前该节点只回纯文本，前端右栏（只在拿到 ChefAnswer 时渲染）
        完全空白，等于"拦是拦住了，但没有任何可见证据"。
        """
        with patch("agent_graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.allergen_block_node({
                "messages": [HumanMessage(content="今晚吃什么")],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        payload = json.loads(result["messages"][0].content)
        self.assertEqual(payload["recipes"], [])
        blocked_items = [g for g in payload["guardrails"] if g["status"] == "blocked"]
        self.assertEqual(len(blocked_items), 1)
        self.assertIn("甲壳纲", blocked_items[0]["condition"])
        self.assertNotIn("已调整", blocked_items[0]["reason"])

    def test_blocked_status_never_rendered_as_adjusted(self):
        """分支顺序回归：blocked 必须先判，否则会落进 adjusted 分支变成不实表述。"""
        items = agent_graph._build_guardrails(
            "今晚吃什么",
            "blocked",
            ["过敏原:甲壳纲类动物及其制品"],
        )
        allergen_items = [i for i in items if i.condition.startswith("过敏原:")]
        self.assertEqual(len(allergen_items), 1)
        self.assertEqual(allergen_items[0].status, "blocked")
        self.assertNotIn("已自动调整", allergen_items[0].reason)

    def test_safety_node_uses_optional_allergen_rules(self):
        with (
            patch("agent_graph._allergens_for_audit", return_value=["芝麻过敏"]),
            patch(
                "agent_graph.suggest_safe_dishes",
                return_value=["清炒青菜"],
            ) as suggest,
        ):
            agent_graph.allergen_block_node({
                "messages": [],
                "verify_violated": ["过敏原:芝麻及其制品"],
            })
        self.assertTrue(suggest.call_args.kwargs.get("use_optional"))

    def test_safety_node_does_not_expand_one_member_allergy_to_whole_family(self):
        with (
            patch("agent_graph._allergens_for_audit", return_value=["虾"]),
            patch("agent_graph.suggest_safe_dishes", return_value=["清炒青菜"]),
        ):
            result = agent_graph.allergen_block_node({
                "messages": [],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        payload = json.loads(result["messages"][0].content)
        self.assertIn("其他无该过敏原的成员仍可按原菜就餐", payload["opening"])
        self.assertNotIn("全家过敏原并集", payload["opening"])


class TestStructuredReaudit(unittest.TestCase):
    def test_structured_card_is_dropped_if_model_reintroduces_allergen(self):
        answer = ChefAnswer(
            recipes=[
                Recipe(
                    name="花生拌面",
                    intro="香脆爽口",
                    difficulty=1,
                    nutrition=3,
                    seasonings=[Seasoning(name="花生酱", amount="2勺")],
                    steps=["面条煮熟后拌入花生酱。"],
                )
            ],
            chef_tip="",
        )
        state = {
            "messages": [
                HumanMessage(content="推荐一道面"),
                AIMessage(content="推荐清汤面"),
            ]
        }
        with (
            patch("agent_graph._allergens_for_audit", return_value=["花生"]),
            patch("agent_graph._build_structure_context", return_value=("可结构化上下文", None, False)),
            patch("agent_graph.build_structured_answer", return_value=answer),
        ):
            result = agent_graph.structure_answer_node(state)
        self.assertEqual(result["verify_status"], "blocked")
        # 卡片必须被丢弃，但仍要输出结构化 payload：
        # 只回空消息会让前端右栏拿不到任何证据（拦了却看不见）。
        self.assertEqual(len(result["messages"]), 1)
        payload = json.loads(result["messages"][0].content)
        self.assertEqual(payload["recipes"], [])
        blocked_items = [g for g in payload["guardrails"] if g["status"] == "blocked"]
        self.assertEqual(len(blocked_items), 1)
        # 拦截 ≠ 已调整：blocked 场景不能说成"已自动调整至合规"
        self.assertNotIn("已调整", blocked_items[0]["reason"])


class TestGuardrailVisibilityAndStream(unittest.TestCase):
    def test_build_guardrails_includes_allergen(self):
        with patch("agent_graph._allergens_for_audit", return_value=["虾"]):
            items = agent_graph._build_guardrails(
                "今晚吃什么",
                "ok",
                ["过敏原:甲壳纲类动物及其制品"],
            )
        allergen_items = [item for item in items if item.condition.startswith("过敏原:")]
        self.assertEqual(len(allergen_items), 1)
        self.assertEqual(allergen_items[0].status, "adjusted")
        self.assertIn("不含", allergen_items[0].rule)

    def test_blocked_message_is_forwarded_to_stream(self):
        def fake_stream(*args, **kwargs):
            yield (
                "updates",
                {
                    "allergen_block": {
                        "messages": [AIMessage(content="过敏原安全替代文案")]
                    }
                },
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(
                HumanMessage(content="今晚吃什么"),
                "allergen-stream-test",
            ))
        self.assertIn(("token", "过敏原安全替代文案"), events)

    def test_prompt_mentions_deterministic_audit(self):
        rendered = main._render_health_profile({"allergens": ["虾"]})
        self.assertIn("硬约束", rendered)
        self.assertIn("确定性审计", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
