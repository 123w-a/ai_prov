# tests/agent/test_ingredient_gate.py
# 冰箱图片食材确认门测试（全部确定性，不调 LLM / 不联网）：
#   上传图片 -> 视觉识别草稿 -> 展示清单要求确认 -> 用户纠正/确认/跳过
#   -> 确认后才进入既有「编号候选」基线流程。
#   基线不受影响：普通文字明确列食材仍直接进入候选推荐，不经此门。
import unittest

from langchain_core.messages import AIMessage, HumanMessage

import agent.graph as g


def _vision_message(user_text, vision_text):
    """复刻 main.build_human_message 的图文消息：用户文字 + 视觉识别标记。"""
    return HumanMessage(
        content=(
            f"{user_text}\n\n"
            "【视觉模型识别结果（客观事实，仅用于本轮推理）】\n"
            f"{vision_text}"
        )
    )


def _failed_vision_message(user_text):
    return HumanMessage(
        content=(
            f"{user_text}\n\n"
            "【图片识别未完成】当前视觉服务不可用，"
            "请明确告诉我图片里的食材和数量，我再继续推荐。"
        )
    )


class VisualDraftTest(unittest.TestCase):
    """视觉文本 -> 待确认食材草稿（确定性、不调用模型）"""

    def test_vision_text_produces_draft(self):
        out = g.ingredient_gate_node({
            "messages": [_vision_message(
                "看看冰箱能做什么",
                "可见鸡蛋 3 个、青菜一把、豆腐一盒。",
            )]
        })
        self.assertTrue(out["ingredient_gate_active"])
        self.assertEqual(out["ingredient_gate_action"], "ask")
        self.assertFalse(out["ingredient_gate_ready"])
        self.assertEqual(
            g._ingredient_names(out["ingredient_draft"]),
            ["鸡蛋", "青菜", "豆腐"],
        )

    def test_uncertain_item_excluded_from_draft(self):
        # 「疑似」项不进清单，避免把不确定识别当成既定食材。
        draft = g._extract_ingredient_draft(
            "冰箱里疑似有牛肉，鸡蛋2个、青菜一把。"
        )
        self.assertEqual(g._ingredient_names(draft), ["鸡蛋", "青菜"])

    def test_confirm_text_lists_draft_and_asks(self):
        state = {
            "ingredient_draft": [{"name": "鸡蛋", "quantity": ""},
                                 {"name": "青菜", "quantity": ""}],
            "ingredient_gate_action": "ask",
        }
        out = g.ingredient_confirm_node(state)
        self.assertEqual(len(out["messages"]), 1)
        self.assertIsInstance(out["messages"][0], AIMessage)
        text = out["messages"][0].content
        self.assertIn("鸡蛋", text)
        self.assertIn("青菜", text)
        self.assertIn("确认", text)

    def test_ask_route_while_gate_open(self):
        out = g.ingredient_gate_node({
            "messages": [_vision_message("看看冰箱", "鸡蛋2个")]
        })
        self.assertEqual(g.ingredient_gate_route(out), "ask")


class ConfirmationTest(unittest.TestCase):
    """用户明确同意 / 跳过确认后才可进入候选推荐"""

    def _state_after_image(self, draft_names):
        draft = [{"name": n, "quantity": ""} for n in draft_names]
        image = _vision_message("看看冰箱", "鸡蛋、青菜")
        confirm = AIMessage(content="我先根据图片整理出这些食材：…请确认。")
        return {
            "messages": [image, confirm, HumanMessage(content="确认无误")],
            "ingredient_gate_active": True,
            "ingredient_draft": draft,
        }

    def test_confirmation_enters_candidate(self):
        state = self._state_after_image(["鸡蛋", "青菜"])
        out = g.ingredient_gate_node(state)
        self.assertTrue(out["ingredient_gate_ready"])
        self.assertFalse(out["ingredient_gate_active"])
        self.assertEqual(out["ingredient_gate_action"], "confirmed")
        self.assertEqual(g.ingredient_gate_route(out), "ready")

    def test_skip_markers_enter_candidate(self):
        for text in ["不用确认，直接推荐", "直接推荐吧", "跳过确认，开始推荐"]:
            with self.subTest(text=text):
                state = self._state_after_image(["鸡蛋", "青菜"])
                state["messages"][-1] = HumanMessage(content=text)
                out = g.ingredient_gate_node(state)
                self.assertTrue(out["ingredient_gate_ready"])
                self.assertEqual(out["ingredient_gate_action"], "skip")

    def test_confirmation_is_not_faked_by_correction_markers(self):
        # 「可以加个鸡蛋 / 还有豆腐」是在改清单，不能当纯确认放行。
        for text in ["可以加个鸡蛋", "还有豆腐", "我有问题"]:
            with self.subTest(text=text):
                self.assertFalse(g._is_ingredient_gate_confirmation(text))
        for text in ["确认无误", "好的，就这些", "没问题", "都对的"]:
            with self.subTest(text=text):
                self.assertTrue(g._is_ingredient_gate_confirmation(text))

    def test_ready_state_counts_as_candidate_turn(self):
        # 确认后进入的是「编号候选」阶段：既不算候选选定，也不直接烧图出卡片。
        messages = [HumanMessage(content="确认无误")]
        self.assertTrue(
            g._is_candidate_turn(messages, ingredient_gate_ready=True)
        )
        self.assertFalse(g._wants_recipe_images(messages))


class CorrectionTest(unittest.TestCase):
    """确定性 add/remove/replace：确认门内不调用推荐模型"""

    BASE = [{"name": "鸡蛋", "quantity": ""},
            {"name": "豆腐", "quantity": ""},
            {"name": "青菜", "quantity": ""}]

    def _draft_names(self, text):
        draft, _changed = g._apply_ingredient_corrections(self.BASE, text)
        return g._ingredient_names(draft)

    def test_replace_with_negation(self):
        self.assertEqual(
            set(self._draft_names("不是鸡蛋，是牛肉")),
            {"牛肉", "豆腐", "青菜"},
        )

    def test_replace_with_verb(self):
        self.assertEqual(
            set(self._draft_names("鸡蛋换成牛肉")),
            {"牛肉", "豆腐", "青菜"},
        )

    def test_remove_then_add_in_one_turn(self):
        self.assertEqual(
            set(self._draft_names("去掉豆腐，再加番茄")),
            {"鸡蛋", "青菜", "番茄"},
        )

    def test_add_ingredient(self):
        self.assertEqual(
            set(self._draft_names("还有番茄")),
            {"鸡蛋", "豆腐", "青菜", "番茄"},
        )

    def test_correction_round_stays_in_gate(self):
        state = {
            "messages": [
                _vision_message("看看冰箱", "鸡蛋、豆腐、青菜"),
                AIMessage(content="请确认。"),
                HumanMessage(content="去掉豆腐，再加番茄"),
            ],
            "ingredient_gate_active": True,
            "ingredient_draft": list(self.BASE),
        }
        out = g.ingredient_gate_node(state)
        self.assertTrue(out["ingredient_gate_active"])
        self.assertFalse(out["ingredient_gate_ready"])
        self.assertEqual(out["ingredient_gate_action"], "corrected")
        self.assertEqual(
            set(g._ingredient_names(out["ingredient_draft"])),
            {"鸡蛋", "青菜", "番茄"},
        )
        self.assertEqual(g.ingredient_gate_route(out), "ask")

    def test_corrected_confirm_text_uses_update_wording(self):
        out = g.ingredient_confirm_node({
            "ingredient_draft": [{"name": "鸡蛋", "quantity": ""}],
            "ingredient_gate_action": "corrected",
        })
        self.assertIn("已按你的说明更新清单", out["messages"][0].content)


class PlainTextPathTest(unittest.TestCase):
    """普通文字路径不受确认门影响，仍直接进入候选推荐基线"""

    def test_plain_text_ingredients_skip_gate(self):
        out = g.ingredient_gate_node({
            "messages": [HumanMessage(content="我有鸡胸肉和青菜")]
        })
        self.assertFalse(out["ingredient_gate_active"])
        self.assertFalse(out["ingredient_gate_ready"])
        self.assertEqual(g.ingredient_gate_route(out), "ready")

    def test_plain_text_is_candidate_turn(self):
        messages = [HumanMessage(content="我有鸡胸肉和青菜")]
        self.assertTrue(g._is_candidate_turn(messages))


class VisionFailureTest(unittest.TestCase):
    """视觉失败时降级为追问，且用户可直接口述食材补齐草稿"""

    def test_failure_yields_empty_draft_and_asks(self):
        out = g.ingredient_gate_node({
            "messages": [_failed_vision_message("看看冰箱能做什么")]
        })
        self.assertTrue(out["ingredient_gate_active"])
        self.assertEqual(out["ingredient_draft"], [])
        self.assertFalse(out["ingredient_gate_ready"])
        self.assertEqual(g.ingredient_gate_route(out), "ask")

    def test_empty_draft_prompts_user_to_list_ingredients(self):
        out = g.ingredient_confirm_node({
            "ingredient_draft": [],
            "ingredient_gate_action": "ask",
        })
        text = out["messages"][0].content
        self.assertIn("告诉我冰箱里有哪些食材", text)

    def test_oral_ingredient_list_fills_empty_draft(self):
        state = {
            "messages": [
                _failed_vision_message("看看冰箱能做什么"),
                AIMessage(content="请直接告诉我冰箱里有哪些食材。"),
                HumanMessage(content="鸡蛋、青菜、豆腐"),
            ],
            "ingredient_gate_active": True,
            "ingredient_draft": [],
        }
        out = g.ingredient_gate_node(state)
        self.assertTrue(out["ingredient_gate_active"])
        self.assertEqual(out["ingredient_gate_action"], "corrected")
        self.assertEqual(
            g._ingredient_names(out["ingredient_draft"]),
            ["鸡蛋", "青菜", "豆腐"],
        )
        self.assertFalse(out["ingredient_gate_ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
