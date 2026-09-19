# tests/test_candidate_flow.py
# 两阶段点菜交互测试（全部确定性，不调 LLM / 不联网）：
#   第一阶段 泛推荐 → 候选清单（不出卡片不配图）
#   第二阶段 「就第2个」 → 解析到具体菜名 → 单卡片 + 配图
# 同时覆盖两个交互修复：追问轮次不出卡片、就地补图不新增卡片。
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

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
            "油炸花生米怎么做",
            "油炸花生米做法",
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

    def test_narrative_requirement_is_not_a_dish_name(self):
        """回归：需求句里的「再给我完整做法」不能被当成点名一道菜。

        实测事故：用户写「…等我选定后，再给我完整做法、营养估算、运动当量和一张对应的
        成品图」，`_DISH_HOWTO_PATTERN` 命中「再给我完整做法」→ 截出「再给我完整」→
        判成点名一道具体的菜 → 整轮跳过候选清单直接出单卡，且卡片菜名就是这句话。
        """
        long_request = (
            "我今晚想吃清淡、低盐、少油的晚餐。家里有鸡蛋、西红柿、青椒、米饭和鸡胸肉。"
            "我有高血压，并且对花生和芝麻过敏。请先给我 3 个候选菜名，只列候选，"
            "不要直接展开完整菜谱。等我选定后，再给我完整做法、营养估算、运动当量"
            "和一张对应的成品图。如果家里食材不够，也请明确告诉我缺什么。"
        )
        self.assertFalse(g.is_specific_dish_request(long_request))
        # 单句形式同样不能命中
        self.assertFalse(g.is_specific_dish_request("给我完整做法"))
        self.assertFalse(g.is_specific_dish_request("再给我详细步骤"))
        self.assertFalse(g.is_specific_dish_request("请告诉我做法"))


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

    def test_confirm_execution_after_card_is_not_candidate_turn(self):
        """卡片已交付后，「就按这个方案执行」这类确认语不能退回候选清单。

        回归背景（端到端实测 T1→T2→T3 复现）：T1 出候选清单、T2 选定后出了卡片+配图，
        T3 用户说「就按这个方案执行。请检查一次花生和芝麻风险，并告诉我每人建议吃多少。」
        —— 这句话既不是点名菜、也不含健康问答词，穿透了 `_is_candidate_turn` 的所有过滤，
        被当成新一轮泛推荐，于是 T2 刚交付的卡片和配图被退回成编号候选清单：
        T3 正文变成「1. 低盐番茄滑炒鸡胸肉丝 2. 番茄炒蛋 3. 西红柿鸡胸肉汤，回复序号就行」，
        用户明明已经确认过方案，却拿不到卡片、也拿不到过敏原复查和份量建议。
        """
        card = json.dumps(
            {"answer_kind": "recipe", "recipes": [{"name": "低盐番茄滑炒鸡胸肉丝", "intro": "x"}]},
            ensure_ascii=False,
        )
        for text in [
            "就按这个方案执行。请检查一次花生和芝麻风险，并告诉我每人建议吃多少。",
            "就按这个方案执行",
            "按这个方案做吧，顺便告诉我每人吃多少",
            "照这个方案来，帮我确认下芝麻风险",
            "就这样，开始做吧",
        ]:
            with self.subTest(text=text):
                msgs = [
                    HumanMessage(content="选第2个"),
                    AIMessage(content=card),
                    HumanMessage(content=text),
                ]
                self.assertFalse(g._is_candidate_turn(msgs))

    def test_confirm_execution_without_card_is_not_candidate_turn(self):
        """「就按这个方案执行」本身不含食材/菜名，无论前面是候选还是卡片都不进候选阶段。

        这条锁的是**既有**行为、不是新闸门：`_is_candidate_turn` 末尾还有一道
        「intent == 'other' 且正文没点食材 → False」，而「就按这个方案执行」正好
        既判不出意图、也没有食材词，所以本来就被挡在外面。
        新加的 `_has_delivered_card` 闸门只负责拦住**混杂执行语+需求**的长句
        （见 `test_confirm_execution_after_card_is_not_candidate_turn`），
        不应该顺手放宽这条既有边界。
        """
        msgs = [
            HumanMessage(content="我有鸡蛋和西红柿"),
            AIMessage(content=json.dumps(
                {"answer_kind": "candidates", "candidates": ["番茄炒蛋", "西红柿鸡蛋汤"], "recipes": []},
                ensure_ascii=False)),
            HumanMessage(content="就按这个方案执行"),
        ]
        self.assertFalse(g._is_candidate_turn(msgs))

    def test_generic_recommend_after_card_still_candidate_turn(self):
        """卡片交付后，用户明确又要泛推荐时必须能回候选阶段。

        与 `test_confirm_execution_after_card_is_not_candidate_turn` 互补：
        确认语才拦，新的泛推荐请求不拦，否则用户就再也点不了第二轮菜了。
        注意「还有别的菜吗」这种带目的词却没有食材/菜名的问法，本来就被
        「intent == 'other' 且没点食材 → False」挡住，它**不属于**本闸门放行的范围，
        所以不列进来。
        """
        card = json.dumps(
            {"answer_kind": "recipe", "recipes": [{"name": "低盐番茄滑炒鸡胸肉丝", "intro": "x"}]},
            ensure_ascii=False,
        )
        for text in ["再推荐几道菜", "晚上不知道吃什么"]:
            with self.subTest(text=text):
                msgs = [
                    HumanMessage(content="选第2个"),
                    AIMessage(content=card),
                    HumanMessage(content=text),
                ]
                self.assertTrue(g._is_candidate_turn(msgs))

    def test_injected_selected_candidate_blocks_candidate_turn(self):
        """路由层注入「已选定候选」后，本轮必须按选定落地处理，不能回候选清单。

        回归背景（端到端实测 T2 复现）：路由层靠会话记录里的候选锚点正确解析出
        「第 2 个 = 青椒炒鸡丝」，但 Agent 层读的是 checkpoint 的候选 payload——
        两处锚点不同时在场时 `resolve_candidate_pick` 返回 None，模型就自己去猜
        「第2个」是哪道，实测凭空造了一个候选清单里根本没有的
        「滑炒鸡丝（无青椒·番茄提鲜版）」。

        修法：路由层把解析结果以 `【已选定候选：X】` 控制信令注入 effective_message
        （仅后端可见，`_strip_internal_request_markers` 负责剥离回显），
        并由这里拦住候选阶段。

        注意：单消息快照判不出「选第 2 个」的序号意图（那需要历史里的候选锚点），
        所以这条闸门必须靠信令，不能靠文本正则。
        """
        injected = (
            "【已选定候选：青椒炒鸡丝】\n"
            "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开，"
            "不得改名、不得替换成别的菜。）\n\n"
            "【配图开关：开启】\n选第 2 个，但不要放青椒，改成两人份，盐控制低一些。"
        )
        self.assertEqual(g._extract_selected_candidate(injected), "青椒炒鸡丝")
        self.assertFalse(g._is_candidate_turn([HumanMessage(content=injected)]))
        self.assertTrue(g._wants_recipe_images([HumanMessage(content=injected)]))

    def test_selected_candidate_marker_is_stripped_from_visible_text(self):
        """控制信令绝不能出现在用户可见正文里。"""
        injected = "【已选定候选：青椒炒鸡丝】\n选第 2 个"
        cleaned = g._strip_internal_request_markers(injected)
        self.assertNotIn("已选定候选", cleaned)
        self.assertIn("选第 2 个", cleaned)

    def test_no_injection_keeps_candidate_turn_for_generic_request(self):
        """没有注入信令时，泛推荐请求照旧走候选阶段（不能误伤）。"""
        self.assertTrue(g._is_candidate_turn(self._m("推荐几道清淡少油的菜")))
        self.assertEqual(g._extract_selected_candidate("推荐几道清淡少油的菜"), "")


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

    def test_candidate_payload_survives_structure_parse_failure(self):
        """正文已展示编号候选时，结构化菜谱链解析失败不能吞掉候选锚点。

        真实 T1 回归：模型正文正确列出三道候选，但结构化链连续 parse-fail，
        structure_answer_node 直接从异常分支返回空消息，checkpoint 里没有 candidates。
        下一轮「选第2个」因此找不到锚点，最终无回答、无卡片、无配图。
        """
        with (
            patch(
                "agent_graph.build_structured_answer",
                side_effect=ValueError("structured parse failed"),
            ),
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(self._state(CANDIDATE_LIST_TEXT))
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(
            payload["candidates"],
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )
        self.assertEqual(payload["recipes"], [])
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


class StructureAnswerConclusionSourceTest(unittest.TestCase):
    """卡片 recipes 跟随本轮 Agent 结论，候选锚点不能在结构化阶段二次覆盖。"""

    def test_candidate_anchor_does_not_override_agent_conclusion(self):
        messages = [
            HumanMessage(content="推荐几道菜"),
            AIMessage(content=json.dumps(
                _candidates_payload(["番茄炒蛋", "青菜豆腐汤"]), ensure_ascii=False
            )),
            HumanMessage(content="就第2个，但我刚发现豆腐坏了，换成番茄鸡蛋汤"),
            AIMessage(content="那就改做番茄鸡蛋汤，避开已经坏掉的豆腐。"),
        ]
        conclusion_context = (
            "用户需求：就第2个，但我刚发现豆腐坏了，换成番茄鸡蛋汤\n\n"
            "本轮 Agent 已确认的回答（菜名和食材以此为准）：\n"
            "那就改做番茄鸡蛋汤，避开已经坏掉的豆腐。"
        )
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["番茄鸡蛋汤"])) as build_mock,
            patch("agent_graph._build_structure_context",
                  return_value=(conclusion_context, None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
            patch("agent_graph._family_members", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

        build_mock.assert_called_once_with(conclusion_context)
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual([recipe["name"] for recipe in payload["recipes"]], ["番茄鸡蛋汤"])


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

    def test_execute_confirmed_plan_keeps_image_pipeline(self):
        text = "就按这个方案执行。请检查一次花生和芝麻风险，并告诉我每人建议吃多少。"
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

    def test_structure_context_is_passed_through_untouched(self):
        """结构性锚点只在 chef_think 层生效，结构化链必须拿到**未被改写**的上下文。

        历史：结构化阶段曾把「本轮已选定：X（候选第N道）——只输出这一道，不得换菜名」
        前置进 context。但那会让旧锚点压过本轮结论 —— 用户说「选第2个，但豆腐坏了换成
        番茄鸡蛋汤」时，模型结论已改，卡片却被锚点拉回旧候选（实测「模型懂了、卡片没懂」）。
        现在锚点只约束 chef_think 生成本轮结论，这里改锁「上下文原样透传」。
        选定约束的保障仍在 `chef_agent_node`（见 test_pick_is_pinned_into_agent_prompt）。
        """
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["青菜豆腐汤"])) as mocked_build,
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            g.structure_answer_node(self._state(self.OPENING))
        sent_context = mocked_build.call_args.args[0]
        self.assertEqual(sent_context, "上下文")
        self.assertNotIn("本轮已选定", sent_context)

    def test_pick_is_pinned_into_agent_prompt(self):
        """选定约束的真正落点：chef_think 的提示词必须把「选了哪一道」钉死。

        否则收口/降级路径下模型会自由发挥，正文菜名与用户选的那道对不上。
        """
        state = {
            "messages": [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False)),
                HumanMessage(content="就第2个"),
            ]
        }
        with patch("agent_graph.llm_with_tools") as mocked_llm:
            g.chef_agent_node(state)
        payload = mocked_llm.invoke.call_args.args[0]
        system_text = str(payload[0].content)
        self.assertIn("本轮已选定的候选", system_text)
        self.assertIn("青菜豆腐汤", system_text)
        self.assertIn("不要换菜", system_text)

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


class CandidateRevisionTurnTest(unittest.TestCase):
    """否定上一批推荐时只能重列候选，不能顺手生成单菜卡片。"""

    def _messages(self, user_text, opening=""):
        return [
            HumanMessage(content="推荐几道下酒菜"),
            AIMessage(content=json.dumps(
                _candidates_payload(["甜辣番茄鸡腿", "甜辣拌豆腐", "甜辣蒸茄子"]),
                ensure_ascii=False,
            )),
            HumanMessage(content=user_text),
            AIMessage(content=opening),
        ]

    def test_rhetorical_correction_is_candidate_revision(self):
        messages = self._messages("难道这些是下酒菜吗")
        self.assertTrue(g._is_candidate_revision_turn(messages))
        self.assertEqual(g._classify_turn_intent(messages), "recommend")
        self.assertTrue(g._is_candidate_turn(messages))

    def test_revision_does_not_resolve_as_pick(self):
        messages = self._messages("难道这些是下酒菜吗")
        self.assertIsNone(g.resolve_candidate_pick(messages))
        self.assertFalse(g._is_dish_pick_turn(messages))

    def test_revision_discards_model_recipe_and_keeps_candidates(self):
        messages = self._messages(
            "难道这些是下酒菜吗",
            "不是。给你三道真正的下酒菜：\n\n"
            "1. 甜辣卤鸡蛋 —— 咸香耐吃\n"
            "2. 甜辣拌内酯豆腐 —— 入口即化\n"
            "3. 甜辣蒸茄子 —— 吸汁入味",
        )
        with (
            patch("agent_graph.build_structured_answer",
                  return_value=_chef_answer(["甜辣拌内酯豆腐"])),
            patch("agent_graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent_graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(payload["recipes"], [])
        self.assertFalse(payload["image_requested"])
        self.assertEqual(
            payload["candidates"],
            ["甜辣卤鸡蛋", "甜辣拌内酯豆腐", "甜辣蒸茄子"],
        )


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

    def test_route_keeps_structured_flow_for_named_image_request(self):
        for status in ("ok", "degraded"):
            with self.subTest(status=status):
                self.assertNotEqual(
                    g.verify_route({
                        "messages": [
                            HumanMessage(
                                content="【配图开关：开启】\n油炸花生米怎么做"
                            )
                        ],
                        "verify_status": status,
                        "tool_budget_exhausted": True,
                    }),
                    "plain",
                )


class ToolBudgetTruncationTest(unittest.TestCase):
    """模型一次并排提多个工具时，按余额「截断放行」，不是「整批否决」。

    实测事故（2026-09-17 19:04，session user_6ba4743f26）：选定轮里模型一次并排提 3 个工具
    （web_search ×2 + nutrition_kb_search），旧判定 `used + max(pending, 1) > budget`
    直接判超预算 → 一个工具都不执行 → 上下文里没有 ToolMessage → 收口时 evidence 为空 →
    用户既看不到菜、也没有讲解。断点快照坐实：used=0 / budget=2 / pending=3。
    """

    CALLS = [
        {"name": "web_search", "args": {"query": "咖喱羊肉 家常做法"}, "id": "t1"},
        {"name": "web_search", "args": {"query": "羊肉怎么炖才软烂"}, "id": "t2"},
        {"name": "nutrition_kb_search", "args": {"query": "高血压 少盐 咖喱"}, "id": "t3"},
    ]

    class _FakeExecutor:
        """记录真正被执行的 tool_call，返回等量 ToolMessage。"""

        def __init__(self):
            self.seen_ids = []

        def invoke(self, state):
            calls = list(getattr(state["messages"][-1], "tool_calls") or [])
            self.seen_ids = [c["id"] for c in calls]
            return {"messages": [
                ToolMessage(content="{}", tool_call_id=c["id"], name=c["name"]) for c in calls
            ]}

    def _state(self, **overrides):
        # 经 add_messages 落库，模拟真实 state（消息带 id，截断才删得掉）
        messages = list(add_messages([], [
            HumanMessage(content="推荐几道菜，今晚想解馋放纵一下"),
            AIMessage(content=json.dumps(
                _candidates_payload(["咖喱牛腩", "红烧肉"]), ensure_ascii=False)),
            HumanMessage(content="5吧，想要吃羊肉的，吃不了牛肉"),
            AIMessage(content="", tool_calls=self.CALLS),
        ]))
        base = {
            "messages": messages,
            "tool_calls_in_turn": 0,
            "tool_budget": g.PICK_TURN_TOOL_BUDGET,
            "tool_rounds": 0,
        }
        base.update(overrides)
        return base

    def test_route_passes_as_long_as_balance_left(self):
        # 旧实现这里会返回 tool_budget_exhausted（0+3 > 2）
        self.assertEqual(g.chef_route_with_tool_budget(self._state()), "tools")

    def test_node_executes_only_up_to_balance(self):
        executor = self._FakeExecutor()
        state = self._state()
        with patch.object(g, "tool_executor", executor):
            out = g.run_tools_node(state)
        self.assertEqual(executor.seen_ids, ["t1", "t2"])
        self.assertEqual(out["tool_calls_in_turn"], 2)
        self.assertEqual(out["tool_rounds"], 1)
        # 消息序列必须自洽：原 3 调用的 AI 消息被替换掉，只留 2 个 ToolMessage，
        # 否则下次调用会因为「有 tool_call 却无 ToolMessage」直接 400。
        merged = add_messages(state["messages"], out["messages"])
        ai = [m for m in merged if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)]
        self.assertEqual(len(ai), 1)
        self.assertEqual([c["id"] for c in ai[0].tool_calls], ["t1", "t2"])
        self.assertEqual(
            [m.tool_call_id for m in merged if isinstance(m, ToolMessage)], ["t1", "t2"]
        )

    def test_no_trim_when_within_balance(self):
        executor = self._FakeExecutor()
        state = self._state(tool_budget=g.MAX_TOOL_CALLS_PER_TURN)
        with patch.object(g, "tool_executor", executor):
            out = g.run_tools_node(state)
        self.assertEqual(executor.seen_ids, ["t1", "t2", "t3"])
        self.assertEqual(out["tool_calls_in_turn"], 3)
        merged = add_messages(state["messages"], out["messages"])
        ai = [m for m in merged if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)]
        self.assertEqual(len(ai[0].tool_calls), 3)

    def test_round_cap_ends_tool_loop(self):
        # 循环轮数与并发宽度分开封顶：圈数超了收口，余额用完也收口
        self.assertEqual(
            g.chef_route_with_tool_budget(self._state(tool_rounds=g.MAX_TOOL_ROUNDS)),
            "tool_budget_exhausted",
        )
        self.assertEqual(
            g.chef_route_with_tool_budget(
                self._state(tool_calls_in_turn=g.PICK_TURN_TOOL_BUDGET)
            ),
            "tool_budget_exhausted",
        )


class OpeningCleanupTest(unittest.TestCase):
    """模型把控制 JSON 当正文吐出来时的清洗：单层 / 双层包裹 / 解析失败，都不许漏 JSON。

    实测真实会话里模型会**双层包裹**：外层 {"opening": "```json{...}```"}，
    只剥一层壳的实现会被直接穿透，用户就在气泡里看到一坨 JSON。
    """

    def test_double_wrapped_opening_is_unwrapped(self):
        inner = json.dumps({
            "opening": "好的，换成严格控制调料的番茄鸡蛋羹。",
            "answer_kind": "recipe",
            "recipes": [{"name": "番茄鸡蛋羹"}],
        }, ensure_ascii=False)
        raw = json.dumps({"opening": "```json\n" + inner + "\n```"}, ensure_ascii=False)
        self.assertEqual(g._clean_opening_text(raw), "好的，换成严格控制调料的番茄鸡蛋羹。")

    def test_single_fenced_control_json_yields_inner_opening(self):
        inner = json.dumps(
            {"opening": "只用盐和香油。", "recipes": [{"name": "番茄鸡蛋羹"}]},
            ensure_ascii=False,
        )
        self.assertEqual(g._clean_opening_text("```json\n" + inner + "\n```"), "只用盐和香油。")

    def test_unparseable_json_with_fingerprint_never_leaks(self):
        for bad in ('```json\n{"opening": "好的', '{"recipes": [1,2', '[{"recipes": 1}]'):
            out = g._clean_opening_text(bad)
            self.assertFalse(
                out.lstrip().startswith(("{", "[", "```")), f"控制 JSON 泄漏了: {out!r}"
            )

    def test_unescaped_quote_inside_value_rescues_opening(self):
        """回归 T3：模型在 JSON 字符串**内部**写未转义引号时，正文不能被丢掉。

        实测事故：T3「就按这个方案执行。请检查一次花生和芝麻风险，并告诉我每人
        建议吃多少。」模型产出的 payload 里，`说明` 字段写了
        `风险点在成品"炸粉、脆皮粉、裹粉"——这类复合粉…标注"可能含芝麻"。`，
        未转义的双引号让整包 `json.loads` 失败。旧实现「解析失败 + 带控制键指纹
        → 返回空串」，把模型写好的整篇过敏原审计（3895 字）静默丢弃，
        用户只看到一张 opening 为空的卡片（端到端表现为 chars=0）。

        修法：解析失败时先正则捞回 opening 字段，捞不到才回落空串。
        """
        broken = (
            '{\n'
            '  "opening": "先答你的两件事：花生／芝麻风险逐项过了一遍。",\n'
            '  "recipes": [{"name": "滑炒鸡丝"}],\n'
            '  "allergen_audit": {"说明": "风险点在成品"炸粉、脆皮粉、裹粉"这类复合粉。"}\n'
            '}'
        )
        out = g._clean_opening_text(broken)
        self.assertEqual(out, "先答你的两件事：花生／芝麻风险逐项过了一遍。")

    def test_rescue_still_refuses_non_human_text(self):
        """救援路径同样不能放行 JSON/控制信令残片。"""
        # opening 字段本身又是 JSON → 丢弃
        nested = '{"opening": "{\\"recipes\\": []}", "x": "a"b"}'
        out = g._clean_opening_text(nested)
        self.assertFalse(out.lstrip().startswith(("{", "[", "```")), f"泄漏: {out!r}")
        # 没有 opening 字段 → 仍是空串
        self.assertEqual(g._rescue_opening_field('{"recipes": [1,2'), "")
        # 截断在 opening 字符串中间（没有收尾引号）→ 空串
        self.assertEqual(g._rescue_opening_field('{"opening": "好的'), "")

    def test_plain_text_passes_through(self):
        self.assertEqual(g._clean_opening_text("今晚想吃点清淡的。"), "今晚想吃点清淡的。")
        self.assertEqual(g._clean_opening_text(""), "")

    def test_depth_cap_returns_empty(self):
        raw = json.dumps({"opening": "x"}, ensure_ascii=False)
        for _ in range(g._MAX_OPENING_UNWRAP + 2):
            raw = json.dumps({"opening": raw}, ensure_ascii=False)
        self.assertEqual(g._clean_opening_text(raw), "")

    def test_final_opening_guard_replaces_json_shape(self):
        self.assertEqual(
            g._final_opening_guard('```json\n{"opening": "好的'), g._FINAL_OPENING_FALLBACK
        )
        self.assertEqual(g._final_opening_guard("正常正文"), "正常正文")
        self.assertEqual(g._final_opening_guard(""), "")


def _stale_index_messages():
    """复刻真实失败会话 user_98be9ab536：候选清单 → 「就第2个」出卡 → 「换成第3个」改选。"""
    return [
        HumanMessage(content="我有西红柿和鸡蛋，晚上想做个简单的家常菜"),
        AIMessage(content=json.dumps({
            "opening": "1. 番茄炒蛋 —— 最省事\n2. 番茄鸡蛋羹 —— 口感极软\n3. 番茄鸡蛋汤面 —— 一锅出",
            "answer_kind": "candidates",
            "candidates": ["番茄炒蛋", "番茄鸡蛋羹", "番茄鸡蛋汤面"],
            "recipes": [],
        }, ensure_ascii=False)),
        HumanMessage(content="就第2个"),
        AIMessage(content=json.dumps({
            "opening": "好的，换成严格控制调料的番茄鸡蛋羹。",
            "recipes": [{"name": "番茄鸡蛋羹"}],
        }, ensure_ascii=False)),
        HumanMessage(content="要不换成第3个吧，并且还想要能让他变得酸点，我还多了香肠和鸡腿肉"),
    ]


class StaleIndexRecoveryTest(unittest.TestCase):
    """序号跨轮失效：不许模型自由换菜；要能回收上一份清单并重新登记锚点。"""

    def test_real_failure_sequence_is_detected(self):
        msgs = _stale_index_messages()
        # 冻结语义没被破坏：实时候选锚点依然为空，旧序号依然不算「选定」
        self.assertEqual(g._recent_candidates(msgs), [])
        self.assertIsNone(g.resolve_candidate_pick(msgs))
        self.assertNotEqual(g._classify_turn_intent(msgs), "confirm_one")
        # 但能回收上一份清单 → 给出确定性的「重新列一遍」处理
        self.assertEqual(
            g._older_candidates(msgs), ["番茄炒蛋", "番茄鸡蛋羹", "番茄鸡蛋汤面"]
        )
        self.assertEqual(g._latest_card_recipe_count(msgs), 1)
        self.assertTrue(g._index_ref_without_target(msgs))

    def test_live_candidate_list_is_not_treated_as_stale(self):
        msgs = [
            HumanMessage(content="我有鸡胸肉和青菜，帮我看看能做什么"),
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]), ensure_ascii=False
            )),
            HumanMessage(content="就第2个"),
        ]
        self.assertFalse(g._index_ref_without_target(msgs))
        self.assertEqual(g.resolve_candidate_pick(msgs), (2, "青菜豆腐汤"))

    def test_index_within_multi_dish_card_is_not_stale(self):
        msgs = [
            AIMessage(content=json.dumps(
                {"opening": "两道", "recipes": [{"name": "A"}, {"name": "B"}]},
                ensure_ascii=False,
            )),
            HumanMessage(content="换成第2道吧"),
        ]
        self.assertFalse(g._index_ref_without_target(msgs))

    def _run_structure(self, msgs):
        with patch("agent_graph.build_structured_answer", return_value=_chef_answer(["番茄鸡蛋羹"])), \
                patch("agent_graph._build_structure_context", return_value=("上下文", None, False)), \
                patch("agent_graph._allergens_for_audit", return_value=[]):
            return g.structure_answer_node({
                "messages": msgs, "verify_status": "ok", "verify_violated": [],
            })

    def test_structure_node_re_registers_recovered_list(self):
        # 回收清单后必须重新登记成候选 payload：否则用户下一轮说「就第3个」还是认不出，
        # 会卡在「说了序号 → 清单已过期」的死循环里。
        result = self._run_structure(_stale_index_messages())
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(payload["candidates"], ["番茄炒蛋", "番茄鸡蛋羹", "番茄鸡蛋汤面"])
        self.assertEqual(payload["recipes"], [])
        self.assertFalse(payload["image_requested"])

    def test_no_recovered_list_falls_back_to_no_message(self):
        result = self._run_structure([HumanMessage(content="换成第3个吧，加点鸡腿肉")])
        self.assertEqual(result, {"messages": []})


class OccasionHintTest(unittest.TestCase):
    """候选「推荐理由」的用途锚定（纯函数，离线）

    背景：用户问「推荐几道下酒菜」，候选理由却写成「去皮后脂肪更低 / 爷爷好嚼 / 不费牙」。
    根因是规则只规定了理由的**长度**、没规定**维度**，模型就用默认的营养维度把空填了。
    修法 = 模板里写死维度优先级 + 这里这个确定性用途抽取做兜底。
    """

    def test_extracts_purpose(self):
        cases = {
            "推荐几道下酒菜": "下酒",
            "晚上想喝点啤酒，配什么菜好": "下酒",
            "来几道佐酒的": "下酒",
            "推荐几道宵夜": "宵夜",
            "明天上班带饭，推荐几道": "带饭",
            "家里来客人了，推荐几道菜": "招待",
            "想减脂，推荐几道菜": "减脂",
            "就想解馋过瘾，推荐几道": "解馋",
        }
        for text, expect in cases.items():
            with self.subTest(text=text):
                self.assertEqual(g._occasion_hint(text), expect)

    def test_no_occasion_returns_empty(self):
        """抽不到用途必须返回空串 —— 绝不能硬塞一个用途，否则泛推荐被污染。"""
        for text in ["随便推荐几道家常菜", "推荐几道番茄炒蛋", "用什么食材做什么", "", None]:
            with self.subTest(text=text):
                self.assertEqual(g._occasion_hint(text), "")

    def test_audience_is_not_occasion(self):
        """受众（爷爷/孩子）不算用途 —— 混进来会盖掉用户真正说的用途。

        用户问的是「下酒」，不该被「爷爷能吃」顶掉；受众由家庭档案与约束矩阵处理。
        """
        for text in ["爷爷能吃的菜推荐几道", "给孩子做的菜推荐几道", "长辈适合吃什么"]:
            with self.subTest(text=text):
                self.assertEqual(g._occasion_hint(text), "")

    def test_rule_keeps_allergen_bottom_line(self):
        """底线冻结点：用途锚定可以压低营养维度，但绝不能动摇过敏原。"""
        rule = g._occasion_rule("下酒")
        self.assertIn("下酒", rule)
        self.assertIn("过敏原与致命禁忌照旧不退让", rule)

    def test_candidate_template_declares_dimension_and_bottom_line(self):
        """模板必须同时含：维度优先级 / 约 30 字理由 / 人性化 / 过敏原底线 / opening 收短。"""
        tpl = g.CANDIDATE_LIST_RULE_TEMPLATE.format(count=3)
        for key in ["用途/场合", "约 30 字", "永不退让", "不是「能不能吃」", "人性化", "25 字以内"]:
            with self.subTest(key=key):
                self.assertIn(key, tpl)


if __name__ == "__main__":
    unittest.main()
