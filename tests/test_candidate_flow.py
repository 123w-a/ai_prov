# tests/test_candidate_flow.py
# 两阶段点菜交互测试（全部确定性，不调 LLM / 不联网）：
#   第一阶段 泛推荐 → 候选清单（不出卡片不配图）
#   第二阶段 「就第2个」 → 解析到具体菜名 → 单卡片 + 配图
# 同时覆盖两个交互修复：追问轮次不出卡片、就地补图不新增卡片。
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

import agent_graph as g
import sessions_store
from agent_schemas import ChefAnswer, Recipe, Seasoning
from api.routes import chat_route

CANDIDATE_LIST_TEXT = (
    "你冰箱里的鸡胸肉和青菜，可以这样做：\n"
    "\n"
    "1. 鸡胸肉炒青菜 —— 高蛋白低脂，10分钟出锅\n"
    "2. 青菜豆腐汤 —— 清淡好消化，5分钟\n"
    "3. 鸡胸肉蔬菜沙拉 —— 免开火，减脂首选\n"
    "\n"
    "回复序号就行，我再把这一道的完整做法给你。"
)


def _candidates_payload(names):
    return {
        "opening": CANDIDATE_LIST_TEXT,
        "answer_kind": "candidates",
        "candidates": list(names),
        "recipes": [],
    }


class SpecificDishRequestTest(unittest.TestCase):
    """点名一道菜 vs 只给食材（候选阶段判定的地基）"""

    def test_named_dish_is_specific(self):
        for text in [
            "我想吃番茄炒蛋",
            "帮我做道番茄炒蛋，配张图",
            "想吃个番茄炒蛋",
            "推荐一道清蒸鲈鱼",
            "我想吃鲈鱼",
            "做个红烧肉",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g.is_specific_dish_request(text))

    def test_generic_recommend_is_not_specific(self):
        for text in [
            "我有鸡胸肉和青菜",
            "推荐几道菜",
            "不知道吃什么",
            "帮我做个菜",
            "今晚吃什么",
            "我想吃番茄",  # 只报食材，没有确定做法
            "高血压能吃火锅吗",
            "你好",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g.is_specific_dish_request(text))

    def test_constraints_and_taste_preferences_are_not_specific(self):
        for text in [
            "我冰箱里面有番茄还有芹菜还有羊肉 我想吃的清淡一点 但是我吃不了羊肉的腥味 怎么做呢三个食材都要用上",
            "我现在有西红柿 鱼豆腐 辣椒 还有牛肉 我想要吃辣一点 但是清淡一点",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g.is_specific_dish_request(text))

    def test_search_command_with_named_dish_is_specific(self):
        self.assertTrue(
            g.is_specific_dish_request("联网搜索 我想吃徐福烩饭 怎么做呢")
        )


class CandidateIndexParseTest(unittest.TestCase):
    """「第2个」这类序号必须能确定性解析，且不能把「做2个菜」当成选第2道"""

    def test_parse_index(self):
        for text, expected in [
            ("就第2个", 2),
            ("第二个", 2),
            ("第2道", 2),
            ("第三款", 3),
            ("选2", 2),
            ("就做2", 2),
            ("就要2号", 2),
            ("第十个", 10),
        ]:
            with self.subTest(text=text):
                self.assertEqual(g.parse_candidate_index(text), expected)

    def test_parse_index_none(self):
        # 「做2个菜」是多菜请求，不是选项号
        for text in ["做2个菜", "随便来点", "换一道", "来点清爽的"]:
            with self.subTest(text=text):
                self.assertIsNone(g.parse_candidate_index(text))


class CandidateNameExtractTest(unittest.TestCase):
    """候选菜名以用户真正看到的正文编号为准"""

    def test_extract_numbered_list(self):
        self.assertEqual(
            g._extract_candidate_names(CANDIDATE_LIST_TEXT),
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )

    def test_extract_without_separator_is_dropped(self):
        text = "1. 这是一句没有分隔符也没有菜名的很长的说明文字，应当被丢弃掉\n2. 青菜豆腐汤 —— 清淡"
        self.assertEqual(g._extract_candidate_names(text), ["青菜豆腐汤"])


class CandidateTurnTest(unittest.TestCase):
    """泛推荐轮才进候选阶段；点名一道菜、要图、健康问答都不进"""

    def _m(self, text):
        return [HumanMessage(content=text)]

    def test_generic_recommend_is_candidate_turn(self):
        for text in ["我有鸡胸肉和青菜", "推荐几道菜", "不知道吃什么", "晚上不知道吃什么"]:
            with self.subTest(text=text):
                self.assertTrue(g._is_candidate_turn(self._m(text)))

    def test_named_dish_not_candidate_turn(self):
        for text in ["我想吃番茄炒蛋", "就做番茄炒蛋", "没胃口换一道"]:
            with self.subTest(text=text):
                self.assertFalse(g._is_candidate_turn(self._m(text)))

    def test_explicit_image_request_not_candidate_turn(self):
        # 泛推荐即便带配图开关，也必须先走候选；只有具体菜才能直接出卡片和配图。
        self.assertTrue(g._is_candidate_turn(self._m("【配图开关：开启】\n推荐几道菜")))

    def test_recommend_with_image_keeps_candidate_turn(self):
        self.assertTrue(g._is_candidate_turn(self._m("推荐几道菜，配张图")))


class CandidateIntentTest(unittest.TestCase):
    """有候选锚点时「第2个」才算点菜确认，没有锚点不能瞎认"""

    def test_index_confirm_with_candidates(self):
        msgs = [
            HumanMessage(content="我有鸡胸肉和青菜"),
            AIMessage(content=json.dumps(_candidates_payload(
                ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"]), ensure_ascii=False)),
            HumanMessage(content="就第2个"),
        ]
        self.assertEqual(g._classify_turn_intent(msgs), "confirm_one")
        self.assertEqual(g.resolve_candidate_pick(msgs), (2, "青菜豆腐汤"))

    def test_name_pick_confirm_with_candidates(self):
        msgs = [
            AIMessage(content=json.dumps(_candidates_payload(
                ["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
            HumanMessage(content="就要青菜豆腐汤"),
        ]
        self.assertEqual(g._classify_turn_intent(msgs), "confirm_one")

    def test_seasoning_adjust_after_candidates_is_not_pick(self):
        msgs = [
            AIMessage(content=json.dumps(_candidates_payload(
                ["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
            HumanMessage(content="青菜豆腐汤少放点盐"),
        ]
        self.assertNotEqual(g._classify_turn_intent(msgs), "confirm_one")

    def test_index_without_candidates_is_not_confirm(self):
        msgs = [HumanMessage(content="就第2个")]
        self.assertNotEqual(g._classify_turn_intent(msgs), "confirm_one")
        self.assertIsNone(g.resolve_candidate_pick(msgs))

    def test_stale_candidates_after_newer_turn_are_not_confirm(self):
        msgs = [
            AIMessage(content=json.dumps(_candidates_payload(
                ["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
            HumanMessage(content="你好"),
            AIMessage(content="你好，有什么想吃的可以告诉我。"),
            HumanMessage(content="就第2个"),
        ]
        self.assertNotEqual(g._classify_turn_intent(msgs), "confirm_one")
        self.assertIsNone(g.resolve_candidate_pick(msgs))

    def test_out_of_range_index(self):
        msgs = [
            AIMessage(content=json.dumps(_candidates_payload(["番茄炒蛋"]), ensure_ascii=False)),
            HumanMessage(content="就第5个"),
        ]
        self.assertIsNone(g.resolve_candidate_pick(msgs))


class RoutePickedCandidateTest(unittest.TestCase):
    """路由层解析「就第2个」：拿到菜名才算选定"""

    def test_resolve_from_store(self):
        with patch(
            "sessions_store.find_recent_candidates",
            return_value={"record_id": 7, "candidates": ["鸡胸肉炒青菜", "青菜豆腐汤"]},
        ):
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "就第2个"), "青菜豆腐汤"
            )
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "第一道"), "鸡胸肉炒青菜"
            )

    def test_resolve_none_cases(self):
        with patch("sessions_store.find_recent_candidates", return_value=None):
            self.assertIsNone(chat_route._resolve_picked_candidate("s1", "就第2个"))
        with patch(
            "sessions_store.find_recent_candidates",
            return_value={"record_id": 7, "candidates": ["鸡胸肉炒青菜"]},
        ):
            self.assertIsNone(chat_route._resolve_picked_candidate("s1", "就第5个"))
            self.assertIsNone(chat_route._resolve_picked_candidate("s1", "随便来点"))


def _chef_answer(names):
    """构造最小 ChefAnswer：structure_answer_node 里的 LLM 调用一律打桩，测试不联网。"""
    return ChefAnswer(
        recipes=[
            Recipe(
                name=name,
                intro="快手家常",
                difficulty=1,
                nutrition=3,
                seasonings=[Seasoning(name="盐", amount="少许")],
                steps=["处理食材", "下锅炒熟"],
            )
            for name in names
        ],
        chef_tip="",
    )


class StructureAnswerCandidateTest(unittest.TestCase):
    """泛推荐轮的 structure_answer 必须产出候选 payload，且不能带 recipes"""

    def _state(self, opening):
        return {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜，帮我看看能做什么"),
                AIMessage(content=opening),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }

    def _patches(self, answer, allergens=None):
        return (
            patch("agent_graph.build_structured_answer", return_value=answer),
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=list(allergens or [])),
        )

    def test_candidate_payload_has_no_recipes(self):
        names = ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"]
        p1, p2, p3 = self._patches(_chef_answer(names))
        with p1, p2, p3:
            result = g.structure_answer_node(self._state(CANDIDATE_LIST_TEXT))
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(payload["recipes"], [])
        self.assertEqual(payload["candidates"], names)
        self.assertFalse(payload["image_requested"])

    def test_unparseable_list_falls_back_to_no_message(self):
        # 正文没有编号清单、结构化结果也只有一道菜时：宁可不登记，也不硬塞卡片
        p1, p2, p3 = self._patches(_chef_answer(["青菜豆腐汤"]))
        with p1, p2, p3:
            result = g.structure_answer_node(self._state("随便给你看看有什么菜吧。"))
        self.assertEqual(result, {"messages": []})

    def test_allergen_filter_drops_bad_candidate(self):
        p1, p2, p3 = self._patches(_chef_answer(["鸡胸肉炒青菜"]), allergens=["花生"])
        with p1, p2, p3, patch(
            "agent_graph.audit_allergens",
            side_effect=lambda text, allergens, use_optional=False: (
                [{"condition": "过敏原:花生"}] if "沙拉" in str(text) else []
            ),
        ):
            result = g.structure_answer_node(self._state(CANDIDATE_LIST_TEXT))
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["candidates"], ["鸡胸肉炒青菜", "青菜豆腐汤"])

    def test_candidates_are_aggregated_across_safety_retry(self):
        """安全护栏重试后只回一道时，仍保留第一段已展示的候选编号锚点。"""
        state = {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜，帮我看看能做什么"),
                AIMessage(content=CANDIDATE_LIST_TEXT),
                HumanMessage(content="[健康护栏审核] 请剔除不合规搭配后重生成"),
                AIMessage(content="已剔除不合规搭配，安全候选如下：\n\n1. 鸡胸肉炒青菜 —— 安全"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        p1, p2, p3 = self._patches(_chef_answer(["鸡胸肉炒青菜"]))
        with p1, p2, p3:
            result = g.structure_answer_node(state)
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(
            payload["candidates"],
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )


class VerifyCandidateTurnTest(unittest.TestCase):
    """候选轮只硬审计菜名，安全说明中的过敏原词不能触发无谓重生成。"""

    def _state(self):
        return {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜，帮我看看能做什么"),
                AIMessage(
                    content=(
                        CANDIDATE_LIST_TEXT
                        + "\n注意：花生过敏的人不能吃这一道，请换成不含花生的候选。"
                    )
                ),
            ],
            "verify_attempts": 0,
            "verify_violated": [],
        }

    def test_candidate_safety_warning_does_not_trigger_retry(self):
        with (
            patch.object(g, "_merged_conditions", return_value=[]),
            patch.object(g, "_allergens_for_audit", return_value=["peanut"]),
            patch.object(g, "audit", return_value=[]),
            patch.object(g, "audit_allergens", return_value=[]) as audit_mock,
        ):
            result = g.verify_answer_node(self._state())

        self.assertEqual(result["verify_status"], "ok")
        audited_text = audit_mock.call_args.args[0]
        self.assertIn("鸡胸肉炒青菜", audited_text)
        self.assertNotIn("花生过敏的人", audited_text)

    def test_candidate_retry_keeps_candidate_format(self):
        violation = {
            "condition": "过敏原:花生",
            "keyword": "花生",
            "message": "花生过敏必须完全避开",
            "source": "GB 7718-2011 4.4.3",
        }
        with (
            patch.object(g, "_merged_conditions", return_value=[]),
            patch.object(g, "_allergens_for_audit", return_value=["peanut"]),
            patch.object(g, "audit", return_value=[]),
            patch.object(g, "audit_allergens", return_value=[violation]),
        ):
            result = g.verify_answer_node(self._state())

        self.assertEqual(result["verify_status"], "retry")
        feedback = result["messages"][0].content
        self.assertIn("只输出编号候选菜名", feedback)
        self.assertIn("不要输出完整做法", feedback)


class AskTurnNoCardTest(unittest.TestCase):
    """追问轮次不出卡片（正文整段是一条追问时保持纯文本）"""

    def test_structure_answer_skips_card_on_asking_turn(self):
        state = {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content="想确认一下，你更想吃清淡的还是重口的？"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        with patch.object(g, "build_structured_answer") as mocked:
            result = g.structure_answer_node(state)
        self.assertEqual(result, {"messages": []})
        mocked.assert_not_called()

    def test_recipe_text_ending_with_question_keeps_card(self):
        # 有成形菜谱结构时，结尾的追问不能把卡片丢掉
        self.assertFalse(g._is_ask_turn(
            "番茄炒蛋做法如下：1. 打蛋。2. 下锅翻炒 3 分钟，加盐 2 克。需要我换成少油版吗？"
        ))
        self.assertTrue(g._is_ask_turn("想确认一下，你更想吃清淡的还是重口的？"))
        self.assertTrue(g._is_ask_turn("你是想问这道菜的做法吗？"))

    def test_verify_route_plain_on_asking_turn(self):
        state = {
            "verify_status": "degraded",
            "messages": [AIMessage(content="想确认一下，你更想吃清淡的还是重口的？")],
        }
        self.assertEqual(g.verify_route(state), "plain")

    def test_verify_route_keeps_safety_states(self):
        self.assertEqual(
            g.verify_route({"verify_status": "retry", "messages": []}), "retry"
        )
        self.assertEqual(
            g.verify_route({"verify_status": "blocked", "messages": []}), "blocked"
        )

    def test_verify_route_normal_answer_still_structures(self):
        state = {
            "verify_status": "ok",
            "messages": [AIMessage(content="番茄炒蛋：先打蛋，下锅翻炒 3 分钟。")],
        }
        self.assertEqual(g.verify_route(state), "ok")


class RestaurantSceneTest(unittest.TestCase):
    """「出去吃」必须和「外出就餐」同源识别，不再漏判成推荐"""

    def test_chufa_chi_is_restaurant(self):
        for text in ["今天出去吃", "出去吃饭吧", "我们下馆子", "在外吃点什么好"]:
            with self.subTest(text=text):
                self.assertTrue(g._is_restaurant_ordering_scene(text))
                self.assertTrue(chat_route._is_restaurant_ordering_scene(text))
                self.assertEqual(g._classify_turn_intent([HumanMessage(content=text)]), "restaurant")

    def test_home_cooking_not_restaurant(self):
        self.assertFalse(g._is_restaurant_ordering_scene("不想出去吃，在家做个番茄炒蛋"))


class ImagePipelineGateTest(unittest.TestCase):
    """配图门两层同源：候选阶段不烧图"""

    def test_generic_recommend_no_image(self):
        for text in ["我有鸡胸肉和青菜", "推荐几道菜", "不知道吃什么"]:
            with self.subTest(text=text):
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
                self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_named_dish_keeps_image(self):
        for text in ["我想吃番茄炒蛋", "就做番茄炒蛋", "没胃口换一道"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._should_enable_image_pipeline(text, None))
                self.assertTrue(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_generic_recommend_with_image_does_not_bypass_candidates(self):
        text = "推荐几道菜，配张图"
        self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
        self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))
        self.assertTrue(g._is_candidate_turn([HumanMessage(content=text)]))

    def test_image_action_phrases_are_recognized(self):
        for text in ["配张图片", "我要图片", "要图片", "配图片"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._wants_image(text, None))

    def test_image_only_action_has_no_requested_dish(self):
        for text in ["配张图片", "我要图片", "要图片", "配图片"]:
            with self.subTest(text=text):
                self.assertIsNone(chat_route._extract_requested_dish(text))

    def test_specific_dish_with_image_keeps_pipeline(self):
        text = "我想吃徐福烩饭，配张图"
        self.assertTrue(chat_route._should_enable_image_pipeline(text, None))
        self.assertTrue(g._wants_recipe_images([HumanMessage(content=text)]))


class NonFoodSafetyFollowupTest(unittest.TestCase):
    """用户追问非食材没被用上时只解释，不再附带菜谱卡片。"""

    def test_battery_followup_stays_plain_text(self):
        state = {
            "messages": [
                HumanMessage(content="锂电池怎么没有用上"),
                AIMessage(content="锂电池不是食材，不能放进菜谱。"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        with patch.object(g, "build_structured_answer") as mocked:
            result = g.structure_answer_node(state)
        self.assertEqual(result, {"messages": []})
        mocked.assert_not_called()


class CandidatesStoreTest(unittest.TestCase):
    """候选清单独立字段落库：不污染 answer，也不被后续落库覆盖"""

    def setUp(self):
        self.session = {
            "session_id": "s_cand",
            "title": "t",
            "created_at": "12:00",
            "messages": [{"id": 1, "user_text": "我有鸡胸肉和青菜", "answer": "__pending__"}],
        }

    def _patch(self):
        return patch.multiple(
            sessions_store,
            _read_session=lambda sid: self.session,
            _write_session=lambda data: None,
        )

    def test_set_and_find_candidates(self):
        with self._patch():
            self.assertTrue(
                sessions_store.set_message_candidates(
                    "s_cand", 1, ["鸡胸肉炒青菜", "青菜豆腐汤"]
                )
            )
            found = sessions_store.find_recent_candidates("s_cand")
        self.assertEqual(found["record_id"], 1)
        self.assertEqual(found["candidates"], ["鸡胸肉炒青菜", "青菜豆腐汤"])
        self.assertEqual(found["dish_name"], "鸡胸肉炒青菜")

    def test_candidates_survive_answer_update(self):
        with self._patch():
            sessions_store.set_message_candidates("s_cand", 1, ["青菜豆腐汤"])
            sessions_store.update_message_answer("s_cand", 1, CANDIDATE_LIST_TEXT)
            found = sessions_store.find_recent_candidates("s_cand")
        self.assertEqual(found["candidates"], ["青菜豆腐汤"])
        self.assertEqual(self.session["messages"][0]["answer"], CANDIDATE_LIST_TEXT)

    def test_find_returns_none_without_candidates(self):
        with self._patch():
            self.assertIsNone(sessions_store.find_recent_candidates("s_cand"))

    def test_find_ignores_stale_candidates_after_a_newer_turn(self):
        self.session["messages"].append(
            {"id": 2, "user_text": "你好", "answer": "你好，有什么想吃的可以告诉我。"}
        )
        with self._patch():
            self.assertTrue(
                sessions_store.set_message_candidates(
                    "s_cand", 1, ["鸡胸肉炒青菜", "青菜豆腐汤"]
                )
            )
            self.assertIsNone(sessions_store.find_recent_candidates("s_cand"))


class CandidateAnchorFallbackTest(unittest.TestCase):
    """结构事件缺失时，落库正文仍能建立候选锚点，且不误收普通菜谱步骤。"""

    def test_generic_recommend_text_registers_candidates(self):
        with patch("sessions_store.set_message_candidates", return_value=True) as mocked:
            ok = chat_route._register_candidate_anchor(
                "s1", 7, "我有鸡胸肉和青菜", CANDIDATE_LIST_TEXT
            )
        self.assertTrue(ok)
        mocked.assert_called_once_with(
            "s1",
            7,
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )

    def test_numbered_recipe_steps_are_not_candidates(self):
        recipe_steps = (
            "番茄炒蛋做法：\n"
            "1. 打蛋液\n"
            "2. 番茄切块\n"
            "3. 下锅翻炒"
        )
        with patch("sessions_store.set_message_candidates", return_value=True) as mocked:
            ok = chat_route._register_candidate_anchor(
                "s1", 8, "我想吃番茄炒蛋", recipe_steps
            )
        self.assertFalse(ok)
        mocked.assert_not_called()


class RecentRecipeImageTargetTest(unittest.TestCase):
    """配图动作词不能残留成「片」并误命中更早的羊肉片。"""

    def setUp(self):
        self.session = {
            "session_id": "s_image_target",
            "title": "t",
            "created_at": "12:00",
            "messages": [
                {
                    "id": 2,
                    "user_text": "那换一个辣点的",
                    "answer": json.dumps(
                        {
                            "recipes": [
                                {"name": "酸辣番茄芹菜炒羊肉片（清爽去膻版）"}
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "id": 3,
                    "user_text": "我想吃辣一点但是清淡一点",
                    "answer": json.dumps(
                        {"recipes": [{"name": "番茄鲜椒滑牛肉（微辣·少盐版）"}]},
                        ensure_ascii=False,
                    ),
                },
            ],
        }

    def test_image_only_request_uses_latest_recipe(self):
        with patch.object(sessions_store, "_read_session", return_value=self.session):
            target = sessions_store.find_recent_recipe_for_image("s_image_target", "片")
        self.assertEqual(target["dish_name"], "番茄鲜椒滑牛肉（微辣·少盐版）")

    def test_explicit_older_dish_still_resolves_exact_match(self):
        with patch.object(sessions_store, "_read_session", return_value=self.session):
            target = sessions_store.find_recent_recipe_for_image(
                "s_image_target", "酸辣番茄芹菜炒羊肉片（清爽去膻版）"
            )
        self.assertEqual(target["dish_name"], "酸辣番茄芹菜炒羊肉片（清爽去膻版）")


class BareIndexPickTest(unittest.TestCase):
    """「3吧，但是可以辣一点」这类口语裸序号必须能解析（截图①的根因）

    用户真实说法不是「就第3个」，而是「3吧」——阿拉伯数字 + 语气词，两头都不靠，
    旧规则只认「第N个」或结尾的「选N」，于是序号解析不出来 → 配图门开不了 → 有卡无图。
    """

    def test_bare_number_with_particle(self):
        for text, expected in [
            ("3吧", 3),
            ("2吧，可以更酸一点", 2),
            ("3吧，但是可以辣一点", 3),
            ("3，", 3),
            ("3。", 3),
            ("1！", 1),
            ("2 吧", 2),
        ]:
            with self.subTest(text=text):
                self.assertEqual(g.parse_candidate_index(text), expected)

    def test_bare_number_without_particle_is_not_index(self):
        # 量词紧跟数字时不能当序号，否则「3个人吃」「做2个菜」会被误判成选第 3/2 道
        for text in ["3个人吃", "做2个菜", "12点吃饭", "2026年计划", "5克盐"]:
            with self.subTest(text=text):
                self.assertIsNone(g.parse_candidate_index(text))

    def test_route_layer_resolves_bare_number_pick(self):
        with patch(
            "sessions_store.find_recent_candidates",
            return_value={"record_id": 7, "candidates": ["鸡胸肉炒青菜", "青菜豆腐汤", "番茄牛腩"]},
        ):
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "3吧，但是可以辣一点"),
                "番茄牛腩",
            )


class SelectedDishWithQuestionTest(unittest.TestCase):
    """「已选定 + 顺带问一句」不能被整体当追问砍掉卡片（截图②的根因）"""

    TURN = "2吧，可以更酸一点，但是我的父亲高血压，可以吃这个吗，要吃的话该注意些什么"
    OPENING = "这道菜可以吃，但要注意控盐。需要我换成少钠版吗？"

    def _state(self, opening):
        return {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
                HumanMessage(content=self.TURN),
                AIMessage(content=opening),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }

    def test_ask_turn_and_pick_turn_are_both_true(self):
        # 正文确实是追问，但本轮同时是一次明确选定 —— 两者都为真时不能让追问赢
        self.assertTrue(g._is_ask_turn(self.OPENING))
        self.assertTrue(g._is_dish_pick_turn(self._state(self.OPENING)["messages"]))

    def test_pick_with_question_still_emits_card(self):
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["青菜豆腐汤"])) as mocked_build,
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(self._state(self.OPENING))
        mocked_build.assert_called_once()
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["recipes"][0]["name"], "青菜豆腐汤")

    def test_pick_is_pinned_into_structure_context(self):
        # 选定轮必须把「选了哪一道」钉死喂给结构化链，否则收口路径下会换菜名
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["青菜豆腐汤"])) as mocked_build,
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            g.structure_answer_node(self._state(self.OPENING))
        sent_context = mocked_build.call_args.args[0]
        self.assertIn("本轮已选定：青菜豆腐汤（候选第2道）", sent_context)
        self.assertIn("不得换菜名", sent_context)

    def test_question_without_anchor_is_still_suppressed(self):
        # 窄口子：没有菜名锚点的纯提问必须继续被挡住，否则会答非所问地弹卡片
        state = {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content="想确认一下，你更想吃清淡的还是重口的？"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        self.assertFalse(g._is_dish_pick_turn(state["messages"]))
        with patch.object(g, "build_structured_answer") as mocked:
            self.assertEqual(g.structure_answer_node(state), {"messages": []})
        mocked.assert_not_called()


class ConstraintCorrectionTurnTest(unittest.TestCase):
    """声明式「约束纠正」轮：只回文字，不重出卡片、不改菜名（截图③的根因）

    用户只是在纠正「谁有什么约束」，不是在改菜；放行到结构化链会让模型顺手换掉菜名。
    """

    def test_correction_sentences_are_recognized(self):
        for text in [
            "并没有说弟弟要减重，这个到后面都不需要管",
            "我爸没有高血压，别管这条",
            "外婆不需要忌口，搞错了",
            "弟弟的过敏不用管",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g._is_constraint_correction_turn(text))

    def test_dish_adjust_or_change_is_not_correction(self):
        for text in [
            "爸爸不用减重，做清淡点",
            "不用管我，就做番茄炒蛋",
            "弟弟不需要减重，换一道",
            "我没有说弟弟要减重，但少放点盐",
            "外婆不用忌口，可以吃这个吗？",
            "推荐一道面",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g._is_constraint_correction_turn(text))

    def test_correction_turn_emits_no_card_and_keeps_old_dish_name(self):
        state = {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
                HumanMessage(content="就第2个"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["青菜豆腐汤"]), ensure_ascii=False)),
                HumanMessage(content="并没有说弟弟要减重，这个到后面都不需要管"),
                AIMessage(content="好的，明白了，弟弟的减重约束我不再纳入。"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["柠檬米醋蒸鲈鱼（低温·零含钠调味版）"])) as mocked_build,
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(state)
        # 前置的门都放行了（结构化链确实跑了一次），是「约束纠正轮」把它挡下来的
        mocked_build.assert_called_once()
        self.assertEqual(result, {"messages": []})


    def test_candidates_survive_internal_retry_round(self):
        # 护栏重试注入「[健康护栏审核]」后，候选锚点不能丢（截图② 的真实原因）
        msgs = [
            HumanMessage(content="晚饭想吃得清淡点，家里有鸡胸肉和青菜"),
            AIMessage(content=json.dumps(
                _candidates_payload(["青菜鸡蓉羹", "番茄鸡胸肉丸汤"]), ensure_ascii=False)),
            HumanMessage(content="2吧，可以更酸一点，但是我的父亲高血压，可以吃这个吗"),
            AIMessage(content="能吃，但要注意控钠。"),
            HumanMessage(content="[健康护栏审核] 你给出的方案违反了以下硬禁忌，必须重新生成一道合规的菜。"),
            AIMessage(content="我已经完成了 2 次资料检索，先给你收口。"),
        ]
        self.assertEqual(g.resolve_candidate_pick(msgs), (2, "番茄鸡胸肉丸汤"))
        self.assertTrue(g._is_dish_pick_turn(msgs))
        # 历史摘要同理：注入的摘要不是新的用户轮次
        self.assertEqual(
            g.resolve_candidate_pick([
                AIMessage(content=json.dumps(
                    _candidates_payload(["青菜鸡蓉羹", "番茄鸡胸肉丸汤"]), ensure_ascii=False)),
                HumanMessage(content="[历史对话摘要，供参考] ①食材：鸡肉、青菜。"),
                HumanMessage(content="就第2个"),
            ]),
            (2, "番茄鸡胸肉丸汤"),
        )

    def test_stale_candidates_after_real_user_turn_still_not_confirm(self):
        # 但「真的插了一轮用户对话」时依旧不认旧序号（冻结规则，勿放宽）
        msgs = [
            AIMessage(content=json.dumps(
                _candidates_payload(["青菜鸡蓉羹", "番茄鸡胸肉丸汤"]), ensure_ascii=False)),
            HumanMessage(content="你好"),
            AIMessage(content="你好，有什么想吃的可以告诉我。"),
            HumanMessage(content="就第2个"),
        ]
        self.assertIsNone(g.resolve_candidate_pick(msgs))


class ToolBudgetOnPickTurnTest(unittest.TestCase):
    """选定轮既不要把工具预算搜爆，也不要在预算耗尽时丢掉卡片（截图②的真实主因）

    实测链路：tool_calls_in_turn=3 → tool_budget_exhausted=true → verify_route 直通 pure text
    → 不进结构化节点 → 卡片和配图一起消失。
    """

    TURN = "2吧，可以更酸一点，但是我的父亲高血压，可以吃这个吗，要吃的话该注意些什么"

    def _state(self, turn_text=TURN):
        return {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
                HumanMessage(content=turn_text),
            ]
        }

    def test_pick_turn_gets_tight_budget(self):
        self.assertEqual(
            g._turn_tool_budget(self._state()["messages"]), g.PICK_TURN_TOOL_BUDGET
        )
        self.assertLess(g.PICK_TURN_TOOL_BUDGET, g.MAX_TOOL_CALLS_PER_TURN)

    def test_generic_turn_keeps_default_budget(self):
        self.assertEqual(
            g._turn_tool_budget([HumanMessage(content="帮我推荐几道菜")]),
            g.MAX_TOOL_CALLS_PER_TURN,
        )

    def test_tight_budget_still_allows_one_batched_pair(self):
        # 模型一次批量调 2 个工具时不能一个都跑不了
        base = {
            "messages": [
                HumanMessage(content="就做番茄炒蛋"),
                AIMessage(content="", tool_calls=[
                    {"name": "web_search", "args": {}, "id": "t1"},
                    {"name": "nutrition_kb_search", "args": {}, "id": "t2"},
                ]),
            ]
        }
        self.assertEqual(
            g.chef_route_with_tool_budget(
                {**base, "tool_calls_in_turn": 0, "tool_budget": g.PICK_TURN_TOOL_BUDGET}
            ),
            "tools",
        )
        self.assertEqual(
            g.chef_route_with_tool_budget(
                {**base, "tool_calls_in_turn": g.PICK_TURN_TOOL_BUDGET,
                 "tool_budget": g.PICK_TURN_TOOL_BUDGET}
            ),
            "tool_budget_exhausted",
        )

    def test_route_keeps_structured_flow_on_pick_turn(self):
        # 选定轮 + 预算耗尽：不能直通纯文本，否则卡片和配图一起消失
        self.assertNotEqual(
            g.verify_route({
                **self._state(),
                "verify_status": "degraded",
                "tool_budget_exhausted": True,
            }),
            "plain",
        )

    def test_route_still_plain_without_pick(self):
        self.assertEqual(
            g.verify_route({
                "messages": [HumanMessage(content="你好")],
                "verify_status": "degraded",
                "tool_budget_exhausted": True,
            }),
            "plain",
        )


if __name__ == "__main__":
    unittest.main()
