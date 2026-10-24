# tests/agent/test_ingredient_gate.py
# 拍照食材的**纯函数**测试（全部确定性，不调 LLM / 不联网）：
#   视觉文本 → 结构化草稿（_extract_ingredient_draft）
#   草稿 → 提示词食材约束 / 候选 payload 依据（_ingredient_prompt_block 等）
#
# 2026-10-22：图里的 ingredient_gate / ingredient_confirm 两个节点已删除——
# 视觉识别本来就在图之前（main.py describe_image），让用户再确认一遍等于把做完的
# 事重做一次，还多付一整轮往返；新版书写面早就走 /api/fridge/vision 把结果填进
# 输入框，根本不经过这两个节点。原先测"确认/跳过/纠正/口述补录"的用例随之删除，
# 这里只保留仍然生效的提取与约束逻辑。
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
        """2026-10-22：确认门已删除，改为直接测「视觉文本 → 结构化草稿」这一步。
        原用例调的是 g.ingredient_gate_node，断言 active/action/ready 三个字段；
        现在草稿由 main.build_human_message 算好预置进 state，只测提取结果本身。"""
        draft = g._extract_ingredient_draft("可见鸡蛋 3 个、青菜一把、豆腐一盒。")
        self.assertEqual(
            g._ingredient_names(draft),
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

class PlainTextPathTest(unittest.TestCase):
    """普通文字路径不受确认门影响，仍直接进入候选推荐基线"""

    def test_plain_text_is_candidate_turn(self):
        messages = [HumanMessage(content="我有鸡胸肉和青菜")]
        self.assertTrue(g._is_candidate_turn(messages))


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
