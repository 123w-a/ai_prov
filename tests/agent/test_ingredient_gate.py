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

    def test_structured_vision_text_keeps_only_explicit_food(self):
        vision_text = """
可见食材：
- 红苹果约3个
- 橙色杯子1个
- 橙子2个
- 红色可口可乐罐3罐
- 银白百威啤酒罐4罐
- 泰国象牌苏打水2瓶
- 青椒、茄子、番茄20颗、豆角、黄瓜
- 其他小罐酱料3个
- 玉米2根
- 冷冻肉块2块
- 鱼1块
- 黄色HPP果汁2瓶
- 绿葡萄1袋
- 橙子桃子各1份
- 花生酱1瓶
- 酸黄瓜酱1瓶

数量：
- 目测约1杯
- 约1杯
- 净含量270ml
- 每类至少1份

包装或标签：
- 红色可口可乐罐
- 银白百威啤酒罐
- 泰国象牌苏打水
- 黄色HPP果汁
- 净含量270ml

烹饪状态：
- 冷冻肉块、鱼块
""".strip()

        draft = g._extract_ingredient_draft(vision_text)
        by_name = {item["name"]: item["quantity"] for item in draft}

        self.assertEqual(
            g._ingredient_names(draft),
            [
                "苹果", "橙子", "可口可乐", "百威啤酒", "苏打水",
                "青椒", "茄子", "番茄", "豆角", "黄瓜", "玉米",
                "冷冻肉", "鱼", "果汁", "葡萄", "桃子",
                "花生酱", "酸黄瓜",
            ],
        )
        self.assertEqual(by_name["苹果"], "3个")
        self.assertEqual(by_name["橙子"], "2个")
        self.assertEqual(by_name["可口可乐"], "3罐")
        self.assertEqual(by_name["百威啤酒"], "4罐")
        self.assertEqual(by_name["苏打水"], "2瓶")
        for noise in ("目测约", "约", "净含量", "每类至少", "其他小罐酱料"):
            self.assertNotIn(noise, by_name)

    def test_unstructured_vision_text_ignores_packaging_labels(self):
        draft = g._extract_ingredient_draft(
            "红色可口可乐罐3罐，净含量270ml，目测约1杯，"
            "每类至少1份，还有红苹果约3个。"
        )
        self.assertEqual(g._ingredient_names(draft), ["可口可乐", "苹果"])

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

    def test_add_instruction_phrases_are_not_ingredients(self):
        for text in [
            "不准确，还要加上一个五花肉",
            "不对，再加上一个五花肉",
            "还需要加一个五花肉",
        ]:
            with self.subTest(text=text):
                names = self._draft_names(text)
                self.assertIn("五花肉", names)
                for noise in ("不准确", "不对", "还要", "还需要"):
                    self.assertNotIn(noise, names)

    def test_unknown_ingredient_after_quantity_is_still_added(self):
        self.assertEqual(
            set(self._draft_names("再加点秋葵")),
            {"鸡蛋", "豆腐", "青菜", "秋葵"},
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


class ConfirmedIngredientPromptTest(unittest.TestCase):
    """确认冰箱食材后，泛推荐提示词必须带上数量、并要求开头交代推荐依据。

    实测（2026-10-10，截图）：用户上传冰箱图 -> 确认食材 -> 「够了，开始推荐吧」，
    候选清单开头只写了护栏和目标，没说明是「按已确认的冰箱食材和数量」推的。
    根因：`_ingredient_prompt_block` 只注入食材名、丢掉数量，也从没要求开头交代依据；
    候选规则还把开头压成「一句话 / 25 字以内」，所以模型不会写这句。
    """

    def test_block_keeps_confirmed_quantities(self):
        block = g._ingredient_prompt_block([
            {"name": "鸡蛋", "quantity": "6个"},
            {"name": "青菜", "quantity": "一把"},
            {"name": "豆腐", "quantity": ""},
        ])
        self.assertIn("鸡蛋（6个）", block)
        self.assertIn("青菜（一把）", block)
        self.assertIn("豆腐", block)
        # 没确认数量的食材只写名字，不许凭空补数量
        self.assertNotIn("豆腐（", block)

    def test_block_requires_basis_sentence_and_overrides_short_opening(self):
        block = g._ingredient_prompt_block([{"name": "鸡蛋", "quantity": "6个"}])
        self.assertIn("根据你已确认的冰箱食材和数量", block)
        # 必须显式声明覆盖候选规则的「开头 25 字以内」限制，否则模型会被压回一句话
        self.assertIn("覆盖候选规则里的开头字数限制", block)
        self.assertIn("不要编造数量", block)

    def test_block_is_empty_without_confirmed_ingredients(self):
        # 没有确认清单时不许注入这段：普通候选轮不能被带出「冰箱/依据」字样
        self.assertEqual(g._ingredient_prompt_block([]), "")
        self.assertEqual(g._ingredient_prompt_block(None), "")

    # 实测（2026-10-10，sessions/user_bb3f76aecb.json）：这份 17 样清单里没有
    # 任何绿叶菜，候选第 3 道仍是「蒜香清炒上海青」。同形的还有 user_d0c851642b.json
    # 的「蒜蓉炒菜心」。所以素菜范围必须正向点名，而不是再说一遍「只能用清单」。
    REAL_FRIDGE_DRAFT = [
        {"name": "苹果", "quantity": ""},
        {"name": "橙子", "quantity": ""},
        {"name": "樱桃", "quantity": ""},
        {"name": "葡萄", "quantity": ""},
        {"name": "可口可乐", "quantity": "3罐"},
        {"name": "百威啤酒", "quantity": "4罐"},
        {"name": "苏打水", "quantity": "2瓶"},
        {"name": "果汁", "quantity": "2瓶"},
        {"name": "青椒", "quantity": ""},
        {"name": "茄子", "quantity": ""},
        {"name": "番茄", "quantity": ""},
        {"name": "黄瓜", "quantity": ""},
        {"name": "玉米", "quantity": "2根"},
        {"name": "花生酱", "quantity": ""},
        {"name": "酸黄瓜", "quantity": ""},
        {"name": "冷冻肉", "quantity": "2块"},
        {"name": "五花肉", "quantity": "1块"},
    ]

    def test_block_names_in_list_vegetables_for_side_dishes(self):
        block = g._ingredient_prompt_block(self.REAL_FRIDGE_DRAFT)
        self.assertIn("【素菜/配菜只能用清单里的这些】", block)
        clause = block.split("【素菜/配菜只能用清单里的这些】")[1]
        for name in ["青椒", "茄子", "番茄", "黄瓜", "玉米（2根）"]:
            with self.subTest(name=name):
                self.assertIn(name, clause)
        # 肉、饮料、酱料、腌制品不能混进素菜点名
        for name in ["冷冻肉", "五花肉", "可口可乐", "百威啤酒", "花生酱", "酸黄瓜", "苹果"]:
            with self.subTest(name=name):
                self.assertNotIn(name, clause)
        # 直指观测到的失败形态：清单里没有绿叶菜就不许出现需要绿叶菜的菜名
        self.assertIn("青菜、菜心、菠菜", clause)

    def test_block_without_vegetables_forbids_out_of_list_greens(self):
        block = g._ingredient_prompt_block([
            {"name": "冷冻肉", "quantity": "2块"},
            {"name": "花生酱", "quantity": ""},
        ])
        self.assertNotIn("只能用清单里的这些", block)
        self.assertIn("【素菜/配菜范围】", block)
        self.assertIn("不要为了凑一道素菜去用清单外的蔬菜", block)

    def test_processed_items_are_not_treated_as_vegetables(self):
        self.assertEqual(
            g._ingredient_vegetable_options([
                {"name": "酸黄瓜", "quantity": ""},
                {"name": "果汁", "quantity": "2瓶"},
                {"name": "花生酱", "quantity": ""},
            ]),
            [],
        )


class IngredientBasisInjectionTest(unittest.TestCase):
    """已确认食材的泛推荐轮：候选 payload 里必须真的出现推荐依据句。

    实测（2026-10-10，截图）：提示词要求「第一句先交代依据」，但同一轮里候选规则
    还写着「开头 25 字以内」，两条并列自称最高优先级 —— 模型挑了短的那条，
    第一句只剩护栏。所以除了合并提示词口径，还要在 payload 上做确定性兜底。
    """

    DRAFT = [
        {"name": "番茄", "quantity": ""},
        {"name": "青椒", "quantity": ""},
        {"name": "玉米", "quantity": "2根"},
    ]
    NAMES = ["蒜蓉蒸茄子", "番茄青椒快炒"]

    def _state(self, **overrides):
        state = {"ingredient_gate_ready": True, "ingredient_draft": self.DRAFT}
        state.update(overrides)
        return state

    def test_rule_variant_defers_opening_to_ingredient_block(self):
        default = g._candidate_list_rule([], ingredient_ready=False)
        variant = g._candidate_list_rule([], ingredient_ready=True)
        # 默认口径保持基线不变；已确认食材时才换成「先交代依据」
        self.assertIn("25 字以内", default)
        self.assertNotIn("先交代推荐依据", default)
        self.assertIn("先交代推荐依据", variant)
        self.assertNotIn("25 字以内", variant)

    def test_payload_prepends_basis_when_model_omits_it(self):
        payload = g._candidate_list_payload(
            "这道都少油快手，30分钟内能上桌。",
            self.NAMES,
            "直接推荐吧",
            self._state(),
            ingredient_basis=True,
        )
        self.assertIsNotNone(payload)
        self.assertTrue(payload["opening"].startswith("根据你已确认的食材和数量"))
        self.assertIn("玉米（2根）", payload["opening"])
        self.assertTrue(
            payload["opening"].endswith("这道都少油快手，30分钟内能上桌。")
        )
        self.assertTrue(payload["opening_authoritative"])
        # 依据句只是正文前置：候选轮照旧不出卡片
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(payload["recipes"], [])

    def test_payload_keeps_model_basis_sentence_untouched(self):
        opening = "根据你已确认的食材和数量（番茄、青椒、玉米2根）来推荐：\n\n都少油快手。"
        payload = g._candidate_list_payload(
            opening,
            self.NAMES,
            "直接推荐吧",
            self._state(),
            ingredient_basis=True,
        )
        # 模型自己写了依据句就一字不改，也不标记 authoritative（避免重复前置）
        self.assertEqual(payload["opening"], opening)
        self.assertNotIn("opening_authoritative", payload)

    def test_payload_skips_basis_when_not_confirmed(self):
        payload = g._candidate_list_payload(
            "这道都少油快手。",
            self.NAMES,
            "推荐几道下酒菜",
            {"ingredient_gate_ready": False, "ingredient_draft": self.DRAFT},
            ingredient_basis=False,
        )
        self.assertEqual(payload["opening"], "这道都少油快手。")
        self.assertNotIn("opening_authoritative", payload)

    def test_payload_skips_basis_when_draft_empty(self):
        payload = g._candidate_list_payload(
            "这道都少油快手。",
            self.NAMES,
            "开始推荐吧",
            self._state(ingredient_draft=[]),
            ingredient_basis=True,
        )
        self.assertEqual(payload["opening"], "这道都少油快手。")
        self.assertNotIn("opening_authoritative", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
