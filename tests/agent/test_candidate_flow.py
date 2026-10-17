# tests/agent/test_candidate_flow.py
# 两阶段点菜交互测试（全部确定性，不调 LLM / 不联网）：
#   第一阶段 泛推荐 → 候选清单（不出卡片不配图）
#   第二阶段 「就第2个」 → 解析到具体菜名 → 单卡片 + 配图
# 同时覆盖两个交互修复：追问轮次不出卡片、就地补图不新增卡片。
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

import agent.trace as trace_mod
import agent.graph as g
from storage import sessions as sessions_store
from agent.schemas import ChefAnswer, HealthLight, Recipe, Seasoning, SourceRef
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


def _delivered_card(names, opening="这道菜的完整讲解。", image_url="http://oss/dish.png"):
    """已交付的结构化卡片（recipes 非空）——`_has_delivered_card` 只认这一种形态。"""
    return {
        "opening": opening,
        "answer_kind": "recipe",
        "recipes": [
            {"name": name, "image_url": image_url, "image_ai_generated": False}
            for name in names
        ],
        "image_requested": True,
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
            "我想做一个北京烤鸭",
            "我有鸭子，想做一个北京烤鸭怎么做",
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
            "吃麻薯的话有几种吃法",
            "麻薯有几种吃法",
            "麻薯有哪些做法",
            "番茄怎么做",
            "鸡蛋怎么做",
            "鸡胸肉怎么做",
            "糯米粉怎么做",
            "推荐几道低糖麻薯点心",
            "来几个适合孩子的甜品",
            "下酒菜有哪些",
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

    def test_meal_decision_request_is_not_a_dish_name(self):
        for text in [
            "请根据这张图片帮我做膳食决策",
            "根据图片帮我做饮食决策",
            "按冰箱里的食材做个用餐决策",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g.is_specific_dish_request(text))
                messages = [HumanMessage(content=text)]
                self.assertTrue(g._is_candidate_turn(messages))
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
                self.assertFalse(g._wants_recipe_images(messages))

    def test_visual_context_does_not_pollute_user_intent(self):
        user_text = "请根据这张图片帮我做膳食决策"
        message_text = (
            f"{user_text}\n\n"
            "【视觉模型识别结果（客观事实，仅用于本轮推理）】\n"
            "图片里有番茄和鸡蛋，看起来可以做番茄炒蛋。"
        )
        messages = [HumanMessage(content=message_text)]

        self.assertEqual(g._current_request_text(message_text), user_text)
        self.assertTrue(g._is_candidate_turn(messages))
        self.assertFalse(chat_route._should_enable_image_pipeline(user_text, None))
        self.assertFalse(g._wants_recipe_images(messages))

    def test_search_command_with_named_dish_is_specific(self):
        self.assertTrue(
            g.is_specific_dish_request("联网搜索 我想吃徐福烩饭 怎么做呢")
        )

    def test_named_dish_with_explanation_question_is_still_specific(self):
        """点名一道菜后追问原因，仍要出这道菜的卡片并解释原因。"""
        for text in [
            "帮我做个番茄炒蛋，为什么要先炒蛋",
            "我想吃番茄炒蛋，为什么要先炒蛋",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g.is_specific_dish_request(text))

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
            ("选2吧", 2),
            ("选 2 吧，可以辣一点", 2),
            ("选二吧", 2),
            ("就做2", 2),
            ("就要2号", 2),
            ("做3把", 3),
            ("做三把", 3),
            ("做 3 把。", 3),
            ("第十个", 10),
            ("那就3吧", 3),
            ("那就要3吧", 3),
            ("那就选3吧", 3),
            ("那就3", 3),
            ("就3", 3),
            ("就3吧", 3),
            ("就 2 吧", 2),
            ("就二吧", 2),
        ]:
            with self.subTest(text=text):
                self.assertEqual(g.parse_candidate_index(text), expected)

    def test_parse_index_none(self):
        # 「做2个菜」是多菜请求，不是选项号
        for text in [
            "做2个菜",
            "做3把菜",
            "3把菠菜",
            "随便来点",
            "换一道",
            "来点清爽的",
            # 承接前缀只放开「序号 + 语气词/收尾」：带量词或名词的不能当选定
            "那就2个菜",
            "那就3个人",
            "那就先这样吧",
            "就2个菜",
            "就3个人",
            "就1份吧",
        ]:
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

    def test_extract_bare_numbered_dish_names(self):
        text = "1. 番茄鸡胸肉\n2. 青菜豆腐汤\n3. 鸡蛋羹"
        self.assertEqual(
            g._extract_candidate_names(text),
            ["番茄鸡胸肉", "青菜豆腐汤", "鸡蛋羹"],
        )

    def test_bare_numbered_recipe_steps_are_not_candidates(self):
        text = "1. 切番茄\n2. 加盐\n3. 放入锅中"
        self.assertEqual(g._extract_candidate_names(text), [])

    def test_numbered_bold_recipe_steps_are_not_candidates(self):
        text = (
            "1. **番茄去皮**：番茄划十字，热水烫后去皮。\n"
            "2. **爆辣底**：锅中放少量油，下辣椒和蒜。\n"
            "3. **炒番茄**：下番茄炒出汁。\n"
            "4. **加水煮**：加水和少量盐煮开。\n"
            "5. **淋蛋花**：淋入蛋液。\n"
            "6. **调味**：少油少盐，保持清爽。"
        )
        self.assertEqual(g._extract_candidate_names(text), [])

    def test_action_named_dishes_remain_candidates(self):
        text = (
            "1. 炒青菜 —— 清爽省事，5分钟出锅\n"
            "2. 蒸鲈鱼 —— 少油嫩滑，适合晚餐\n"
            "3. 拌黄瓜 —— 免开火，口感脆爽"
        )
        self.assertEqual(
            g._extract_candidate_names(text),
            ["炒青菜", "蒸鲈鱼", "拌黄瓜"],
        )

    def test_unbolded_colon_recipe_steps_are_not_candidates(self):
        text = (
            "1. 切番茄：番茄切块备用。\n"
            "2. 炒番茄：下锅炒出汁。\n"
            "3. 加盐：少量调味后出锅。"
        )
        self.assertEqual(g._extract_candidate_names(text), [])

    def test_colon_action_step_title_is_not_candidate(self):
        """回归：卡片正文的「调汁：…」步骤标题不能被登记成候选菜名。

        实测 user_777df48ed8 第 5 轮：模型给的「清蒸鲈鱼 · 低盐版」正文里，
        `5. 调汁：生抽5ml＋热水30ml＋白糖1g搅匀…` 被写进会话的 candidates 字段
        （前导动词表里没有「调」），下一轮序号指代就锚到了「调汁」上。
        """
        text = "1. 调汁：生抽15ml＋白糖2g＋热水2汤匙，搅到糖化开。"
        self.assertEqual(g._extract_candidate_names(text), [])

    def test_numbered_prose_line_is_not_candidate(self):
        """回归：编号散文句（含中文句读）不是菜名。

        同轮的另一条污染：`6. 趁热吃，凉了腥味回潮。` 既不以前导动词开头、
        也不以步骤后缀收尾，旧规则放行后被登记成候选。
        """
        text = "6. 趁热吃，凉了腥味回潮。"
        self.assertEqual(g._extract_candidate_names(text), [])

    def test_full_card_body_yields_no_candidates(self):
        """整体回归：真实卡片正文一行候选都不该抽出来。"""
        text = (
            "1. 鱼刮鳞、去腮、去内脏，鱼身两侧各划2刀。\n"
            "2. 盘底横架2根筷子把鱼架空，鱼肚里塞姜片和葱段。\n"
            "3. **水烧到大开再放鱼**，加盖大火蒸8分钟。\n"
            "4. 端出来立刻倒掉盘中腥水，重新铺上葱丝、姜丝。\n"
            "5. 调汁：生抽5ml＋热水30ml＋白糖1g搅匀，沿盘边淋入。\n"
            "6. 趁热吃，凉了腥味回潮。"
        )
        self.assertEqual(g._extract_candidate_names(text), [])


class CandidateTurnTest(unittest.TestCase):
    """泛推荐轮才进候选阶段；点名一道菜、要图、健康问答都不进"""

    def _m(self, text):
        return [HumanMessage(content=text)]

    def test_generic_recommend_is_candidate_turn(self):
        for text in [
            "我有鸡胸肉和青菜",
            "推荐几道菜",
            "不知道吃什么",
            "晚上不知道吃什么",
            "吃麻薯的话有几种吃法",
            "麻薯有哪些做法",
            "推荐几道低糖麻薯点心",
            "来几个适合孩子的甜品",
            "下酒菜有哪些",
            "虾有几种做法",
            "鸡蛋有什么花样",
            "土豆做法大全",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g._is_candidate_turn(self._m(text)))

    def test_explicit_multi_count_recommend_is_candidate_turn(self):
        for text in [
            "今晚想用番茄做几道菜，先推荐三道让我选",
            "推荐3道菜让我选",
            "给我三道番茄菜",
            "推荐10道家常菜",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g.is_specific_dish_request(text))
                self.assertTrue(g._is_candidate_turn(self._m(text)))
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
                self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_single_dish_recommendation_stays_specific(self):
        text = "推荐一道番茄炒蛋"
        self.assertTrue(g.is_specific_dish_request(text))
        self.assertFalse(g._is_candidate_turn(self._m(text)))

    def test_what_can_i_eat_recommendation_stays_candidate_turn(self):
        text = (
            "【实时状态：昨晚没睡好、今天肌肉酸痛、肠胃不太舒服、很累没力气】\n"
            "这样的话我还能吃什么，推荐几道菜品"
        )
        messages = self._m(text)

        self.assertTrue(g._is_candidate_turn(messages))
        self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
        self.assertFalse(g._wants_recipe_images(messages))

    def test_explicit_food_safety_question_is_not_candidate_turn(self):
        for text in ["这个能吃吗", "这个可以吃吗", "能不能吃火锅", "海鲜后能喝牛奶吗"]:
            with self.subTest(text=text):
                self.assertFalse(g._is_candidate_turn(self._m(text)))

    def test_information_questions_are_not_candidate_turn(self):
        for text in [
            "虾怎么处理",
            "牛肉怎么腌",
            "糯米粉怎么挑",
            "麻薯怎么保存",
            "虾和什么不能一起吃",
            "吃麻薯会胖吗",
            "海鲜后能喝牛奶吗",
            "番茄炒蛋和红烧肉哪个更健康",
            "这道和上一道比呢",
            "吃火锅的时候喝什么",
            "别忘了爷爷不能吃虾",
            "你刚才说的放盐是什么意思",
            "这道菜为什么要先炒蛋",
            "为什么焯水这一步不能省",
            "这段讲解里说的火候是什么意思",
            "这道菜适合谁吃",
            "这个菜谱为什么只要三步",
            "前面那个步骤没看懂，解释一下",
            "先告诉我原理，再决定做不做",
            "解释一下番茄炒蛋为什么要先炒蛋",
            "麻薯为什么不能放冰箱",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g._is_candidate_turn(self._m(text)))

    def test_named_dish_not_candidate_turn(self):
        for text in ["我想吃番茄炒蛋", "就做番茄炒蛋"]:
            with self.subTest(text=text):
                self.assertFalse(g._is_candidate_turn(self._m(text)))

    def test_taste_adjustment_after_delivered_card_is_not_candidate_turn(self):
        card = {
            "opening": "番茄鸡蛋汤（减重版）",
            "answer_kind": "recipe",
            "recipes": [{
                "name": "番茄鸡蛋汤（减重版）",
                "image_url": "http://oss/tomato-soup.png",
            }],
        }
        for text in (
            "就 吃这道菜品，能做道更辣些吗",
            "就吃这道菜品，想再改善一下口感",
        ):
            with self.subTest(text=text):
                messages = [
                    HumanMessage(content="2"),
                    AIMessage(content=json.dumps(card, ensure_ascii=False)),
                    HumanMessage(content=text),
                ]
                self.assertEqual(g._classify_turn_intent(messages), "change_one")
                self.assertFalse(g._is_candidate_turn(messages))

    def test_taste_adjustment_never_reuses_previous_card(self):
        """口感调整轮必须回到 Agent 重新生成，不能路由层直接复用旧卡片。"""
        with patch("storage.sessions.find_recent_recipe_for_image") as find_mock:
            result = chat_route._reusable_confirmation_answer(
                "sid",
                "就吃这道菜品，想再改善一下口感",
            )

        self.assertIsNone(result)
        find_mock.assert_not_called()

    def test_taste_adjustment_markers_require_change_context(self):
        for text in [
            "改善口感",
            "调整口味",
            "换个口味",
            "口感嫩一点",
            "口味太重了",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g._is_taste_adjustment_request(text))
        for text in [
            "口味虾怎么做",
            "这道菜口感怎么样",
            "口味很好",
        ]:
            with self.subTest(text=text):
                self.assertFalse(g._is_taste_adjustment_request(text))

    def test_change_one_without_named_dish_returns_to_candidates(self):
        self.assertTrue(g._is_candidate_turn(self._m("没胃口换一道")))

    def test_taste_adjustment_after_delivered_recipe_keeps_image_pipeline(self):
        """口味/口感调整续轮仍要保留配图链路，否则同一道菜第二次会丢图。

        复现证据：`sessions/user_147767ddb1.json` 第 2 轮「选3」出「西红柿鸡蛋汤」
        带图，第 3 轮「想要更辣一点」沿同一道菜重新生成，但路由层
        `_should_enable_image_pipeline` 判为 other → 关配图 → 结构化答案按
        `image_requested=False` 清空 image_url，前端卡片直接丢图。
        """
        with patch(
            "storage.sessions.find_recent_recipe_for_image",
            return_value={"dish_name": "西红柿鸡蛋汤", "recipe_index": 0},
        ):
            self.assertTrue(
                chat_route._should_enable_image_pipeline_for_turn(
                    "s1", "想要更辣一点", None, None
                )
            )
            self.assertTrue(
                g._wants_recipe_images(
                    [HumanMessage(content="【配图开关：开启】\n想要更辣一点")]
                )
            )

    def test_strict_control_is_a_recipe_adjustment(self):
        self.assertTrue(g._is_taste_adjustment_request("要严格控制"))

    def test_explicit_recipe_change_target_keeps_image_pipeline(self):
        self.assertTrue(
            chat_route._has_explicit_recipe_change_target(
                "那换成不放花生的小炒肉行吗"
            )
        )
        self.assertTrue(
            chat_route._should_enable_image_pipeline_for_turn(
                "s1", "那换成不放花生的小炒肉行吗", None, None
            )
        )

    def test_generic_recipe_change_targets_do_not_open_image_pipeline(self):
        """泛化版本或候选序号不能被当成明确目标菜而提前配图。"""
        for text in ("换成低盐版本", "换成第3个吧", "换一道菜"):
            with self.subTest(text=text):
                self.assertFalse(
                    chat_route._has_explicit_recipe_change_target(text)
                )
                self.assertFalse(
                    chat_route._should_enable_image_pipeline_for_turn(
                        "s1", text, None, None
                    )
                )

    def test_taste_adjustment_without_delivered_recipe_stays_plain(self):
        """没有可复用菜谱时，口味调整不能凭空开配图（候选清单轮尤其）。"""
        with patch(
            "storage.sessions.find_recent_recipe_for_image", return_value=None
        ):
            self.assertFalse(
                chat_route._should_enable_image_pipeline_for_turn(
                    "s1", "想要更辣一点", None, None
                )
            )
            # 换一道菜仍回到候选清单，不属于口味调整续轮。
            self.assertFalse(
                chat_route._should_enable_image_pipeline_for_turn(
                    "s1", "换一道菜", None, None
                )
            )

    def test_taste_adjustment_helper_respects_existing_gates(self):
        """候选选定、常规配图判据仍在口味调整分支之前生效。"""
        with patch(
            "storage.sessions.find_recent_recipe_for_image", return_value=None
        ):
            self.assertTrue(
                chat_route._should_enable_image_pipeline_for_turn(
                    "s1", "选3", None, "西红柿鸡蛋汤"
                )
            )

    def test_spoken_taste_variants_keep_same_dish_and_image(self):
        """口语辣度/咸淡续轮也必须保住同一道菜和配图链路。

        复现证据：`sessions/user_ac95d5ea32.json` 第 2 轮选定「西红柿鸡蛋汤」带图，
        第 3 轮「我不太能吃辣，这道汤需要放辣椒吗」沿同一道汤继续，但此前这些
        口语说法都判不出口味调整意图 → 配图门关闭 → 结构化答案清空 image_url，
        卡片既丢了图、菜名也被改成「番茄鸡蛋青椒汤」。
        """
        for text in [
            "我不太能吃辣，这道汤需要放辣椒吗",
            "太辣了",
            "少放点盐",
            "辣度降低一点",
            "我怕辣，辣椒少放点",
        ]:
            with self.subTest(text=text):
                self.assertTrue(g._is_taste_adjustment_request(text))
        with patch(
            "storage.sessions.find_recent_recipe_for_image",
            return_value={"dish_name": "西红柿鸡蛋汤", "recipe_index": 0},
        ):
            for text in [
                "我不太能吃辣，这道汤需要放辣椒吗",
                "太辣了",
                "少放点盐",
            ]:
                with self.subTest(text=text):
                    self.assertTrue(
                        chat_route._should_enable_image_pipeline_for_turn(
                            "s1", text, None, None
                        )
                    )
        # 没有可补图的历史菜谱时，口味说法不能凭空开配图（候选清单轮仍不配图）。
        with patch(
            "storage.sessions.find_recent_recipe_for_image", return_value=None
        ):
            for text in ["太辣了", "少放点盐", "辣度降低一点"]:
                with self.subTest(text=text):
                    self.assertFalse(
                        chat_route._should_enable_image_pipeline_for_turn(
                            "s1", text, None, None
                        )
                    )

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

    def test_selected_candidate_control_sentence_is_stripped_too(self):
        """回归：信令后面那句后端控制说明也必须整块剥掉，不只删标签。

        端到端实测：「3吧」选菜轮工具预算耗尽时，兜底句会原样引用「用户本轮原话」，
        只删 `【已选定候选：X】` 标签会把括号说明留在正文里，于是
        「（用户用序号选定了上一轮候选清单里的这一道…）」被直接推给用户。
        """
        injected = (
            "【已选定候选：青椒炒鸡丝】\n"
            "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开，"
            "不得改名、不得替换成别的菜。）\n\n"
            "【配图开关：开启】\n3吧"
        )
        cleaned = g._strip_internal_request_markers(injected)
        self.assertNotIn("已选定候选", cleaned)
        self.assertNotIn("本轮必须围绕它展开", cleaned)
        self.assertNotIn("不得替换成别的菜", cleaned)
        self.assertNotIn("配图开关", cleaned)
        self.assertEqual(cleaned, "3吧")
        # 剥完仍是用户本轮的真实需求：兜底句引用它时不会带出内部说明。
        self.assertEqual(g._current_request_text(injected), "3吧")

    def test_no_injection_keeps_candidate_turn_for_generic_request(self):
        """没有注入信令时，泛推荐请求照旧走候选阶段（不能误伤）。"""
        self.assertTrue(g._is_candidate_turn(self._m("推荐几道清淡少油的菜")))
        self.assertEqual(g._extract_selected_candidate("推荐几道清淡少油的菜"), "")


class RequestedAllergenAdditionTest(unittest.TestCase):
    """第3道候选被明确改造加入家庭过敏原时，目标、拦截文案和护栏必须同源。"""

    def _messages(self):
        return [HumanMessage(content=(
            "【已选定候选：番茄蒸水蛋】\n"
            "对第3道菜品进行改造，加入芒果这个水果，做成一个甜品可以吗"
        ))]

    def test_injected_target_stays_authoritative(self):
        messages = self._messages()
        self.assertEqual(
            g.selected_target_for_turn(messages),
            (3, "番茄蒸水蛋"),
        )
        self.assertFalse(g._index_ref_without_target(messages))

    def test_deterministic_refusal_names_selected_dish_and_skips_llm(self):
        messages = self._messages()
        with (
            patch("agent.graph.llm_with_tools") as mocked_llm,
            patch("agent.graph._allergens_for_audit", return_value=["芒果"]),
        ):
            result = g.chef_agent_node({"messages": messages})

        mocked_llm.invoke.assert_not_called()
        reply = str(result["messages"][0].content)
        self.assertIn("第3道已选定为「番茄蒸水蛋」", reply)
        self.assertIn("芒果及其制品是硬约束", reply)
        self.assertNotIn("去皮", reply)
        self.assertNotIn("压泥", reply)

    def test_verify_blocks_unresolved_profile_allergen(self):
        messages = self._messages()
        with patch("agent.graph._allergens_for_audit", return_value=["芒果"]):
            result = g.verify_answer_node({
                "messages": messages + [AIMessage(content="不能加入芒果。")],
                "verify_attempts": 0,
                "session_id": "mango-session",
            })

        self.assertEqual(result["verify_status"], "blocked")
        self.assertIn("过敏原:芒果及其制品", result["verify_violated"])

    def test_final_block_payload_names_dish_and_blocks_allergen(self):
        messages = self._messages() + [AIMessage(content="不能加入芒果。")]
        with (
            patch("agent.graph._allergens_for_audit", return_value=["芒果"]),
            patch("agent.graph.suggest_safe_dishes", return_value=["清炒青菜"]),
        ):
            result = g.allergen_block_node({
                "messages": messages,
                "verify_status": "blocked",
                "verify_violated": ["过敏原:芒果及其制品"],
                "session_id": "mango-session",
            })

        payload = json.loads(result["messages"][0].content)
        self.assertIn("第3道已选定为「番茄蒸水蛋」", payload["opening"])
        self.assertIn("家庭画像中已确认芒果及其制品过敏", payload["opening"])
        self.assertEqual(payload["recipes"], [])
        blocked = [
            item for item in payload["guardrails"]
            if item["condition"] == "过敏原:芒果及其制品"
        ]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["status"], "blocked")


class SelectedTargetOpeningPreservationTest(unittest.TestCase):
    """选定目标结构化时，正确讲解原样保留，冲突讲解按同一目标校正。"""

    TARGET = "番茄青椒炒小龙虾（微辣版）"

    def _messages(self):
        return [
            HumanMessage(content="我有小龙虾、朝天椒、车仔面、番茄和青椒，想要清淡一点"),
            AIMessage(content=json.dumps(_candidates_payload([
                "清蒸小龙虾",
                "番茄青椒小龙虾汤面",
                self.TARGET,
            ]), ensure_ascii=False)),
            HumanMessage(content=(
                f"【已选定候选：{self.TARGET}】\n"
                "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开。）\n\n"
                "就要第3道菜品"
            )),
            AIMessage(content=(
                "抱歉，我目前无法确认你说的第3道菜品：上一轮候选清单没有出现在"
                "本轮上下文里。请你把第3道菜名直接发我。"
            )),
        ]

    def _run_structure(self, answer, opening=None):
        messages = self._messages()
        if opening is not None:
            messages[-1] = AIMessage(content=opening)
        with (
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            return g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

    def test_conflicting_opening_is_replaced_with_confirmed_target(self):
        result = self._run_structure(_chef_answer([self.TARGET]))
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["recipes"][0]["name"], self.TARGET)
        self.assertIn("第3道", payload["opening"])
        self.assertIn(self.TARGET, payload["opening"])
        self.assertTrue(payload["opening_authoritative"])
        self.assertNotIn("无法确认", payload["opening"])
        self.assertNotIn("清单没有出现在本轮上下文里", payload["opening"])
        # chef_tip 是模型提供的独立字段；为空时必须保持为空，不能用
        # recipe.intro 或代码生成的新文案偷偷替换既有输出。
        self.assertEqual(payload["chef_tip"], "")

    def test_empty_chef_tip_is_not_replaced_by_code(self):
        answer = _chef_answer([self.TARGET])
        self.assertEqual(answer.chef_tip, "")

    def test_normal_selected_opening_is_preserved(self):
        opening = f"好的，已按你选的第3道「{self.TARGET}」整理，下面把调料和火候收细一点。"
        result = self._run_structure(_chef_answer([self.TARGET]), opening=opening)
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["opening"], opening)
        self.assertNotIn("opening_authoritative", payload)

    def test_selected_opening_without_target_name_is_preserved(self):
        # 2026-10-11 收窄触发口径：模型写了完整讲解但没重复菜名时不得被模板顶掉
        # （基线：选定后保留完整讲解）。只有正文自称候选失效或认成另一道候选菜才校正。
        opening = "好的，已按你选的第3道整理，下面把调料和火候收细一点。"
        result = self._run_structure(_chef_answer([self.TARGET]), opening=opening)
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["opening"], opening)
        self.assertNotIn("opening_authoritative", payload)
        self.assertEqual(payload["recipes"][0]["name"], self.TARGET)

    def test_opening_naming_other_candidate_is_repaired(self):
        # 认成清单里的另一道菜 = 真正的同轮错位，仍要校正回本轮已选目标
        opening = "好的，那就按第2道「番茄青椒小龙虾汤面」来做，下面给完整做法。"
        result = self._run_structure(_chef_answer([self.TARGET]), opening=opening)
        payload = json.loads(result["messages"][-1].content)

        self.assertIn(self.TARGET, payload["opening"])
        self.assertTrue(payload["opening_authoritative"])
        self.assertEqual(payload["recipes"][0]["name"], self.TARGET)

    def test_opening_denying_candidate_index_is_repaired(self):
        """实测 2026-10-15：卡片认对了「第3道」，讲解却自称看不到编号清单，
        并把整道菜换成候选清单里根本没有的另一道（卡片「番茄蒸水蛋」/ 讲解
        「番茄豆腐菌菇汤」）。旧 marker 表只认「看不到前文 / 无法确认」的固定
        说法，这一族措辞漏判后错位讲解原样发给用户——同轮卡片和讲解必须指向
        同一道菜。
        """
        opening = (
            "我这边没有看到你前面列的“第3个”具体是什么内容，当前检索也没有找到"
            "可靠菜谱结果，所以不确定你指的哪一项。\n\n"
            "**番茄豆腐菌菇汤（少油少盐版）**\n食材（1~2人份）\n- 番茄 2个\n"
        )
        result = self._run_structure(_chef_answer([self.TARGET]), opening=opening)
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["recipes"][0]["name"], self.TARGET)
        self.assertIn("第3道", payload["opening"])
        self.assertIn(self.TARGET, payload["opening"])
        self.assertNotIn("番茄豆腐菌菇汤", payload["opening"])
        self.assertNotIn("没有看到你前面列的", payload["opening"])
        self.assertTrue(payload["opening_authoritative"])

    def test_denial_wording_without_candidate_anchor_is_preserved(self):
        # 收窄误伤面：「找不到某样调料」这类正常讲解没有序号/候选锚点，不触发替换
        opening = "好的，这份按低盐做法整理。如果找不到郫县豆瓣酱，用少量生抽代替就行。"
        result = self._run_structure(_chef_answer([self.TARGET]), opening=opening)
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["opening"], opening)
        self.assertNotIn("opening_authoritative", payload)

    def test_empty_opening_is_not_replaced_by_template(self):
        # 正文为空交给前端流式原话兜底，模板不能顶掉用户已经看到的讲解
        result = self._run_structure(_chef_answer([self.TARGET]), opening="")
        payload = json.loads(result["messages"][-1].content)

        self.assertEqual(payload["opening"], "")
        self.assertNotIn("opening_authoritative", payload)

    def test_mismatched_recipe_does_not_claim_selected_target(self):
        result = self._run_structure(_chef_answer(["清蒸小龙虾"]))
        payload = json.loads(result["messages"][-1].content)

        self.assertIn("无法确认", payload["opening"])
        self.assertNotIn("opening_authoritative", payload)


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


class CandidateAckWithoutPickTest(unittest.TestCase):
    """候选后的「行 / xing」只表示收到，不代表选定某一道菜。"""

    def _messages(self, ack):
        return [
            HumanMessage(content="我有鸡胸肉和青菜，帮我看看能做什么"),
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"]),
                ensure_ascii=False,
            )),
            HumanMessage(content=ack),
        ]

    def test_pure_ack_is_detected_only_with_candidates(self):
        for ack in ("xing", "行", "好的。", "可以呀", "嗯嗯", "OK", "没问题"):
            with self.subTest(ack=ack):
                self.assertTrue(g._is_candidate_ack_without_pick(self._messages(ack)))
        self.assertFalse(
            g._is_candidate_ack_without_pick([HumanMessage(content="xing")])
        )

    def test_explicit_pick_is_never_treated_as_pure_ack(self):
        for text in ("2", "2吧", "就第2个", "行，就第2个", "青菜豆腐汤"):
            with self.subTest(text=text):
                msgs = self._messages(text)
                self.assertFalse(g._is_candidate_ack_without_pick(msgs))

    def test_chef_short_circuits_without_calling_model(self):
        with patch("agent.graph.llm_with_tools") as mocked_llm:
            result = g.chef_agent_node({"messages": self._messages("xing")})
        mocked_llm.invoke.assert_not_called()
        reply = str(result["messages"][-1].content)
        self.assertIn("还没指定哪一道", reply)
        self.assertIn("1 到 3", reply)

    def test_structure_re_registers_candidates_without_card_or_image(self):
        msgs = self._messages("xing")
        msgs.append(AIMessage(
            content="收到，不过还没指定哪一道。直接回复 1 到 3 的序号就行。"
        ))
        state = {
            "messages": msgs,
            "verify_status": "ok",
            "verify_violated": [],
        }
        with (
            patch("agent.graph.build_structured_answer") as build_mock,
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(state)
        build_mock.assert_not_called()
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(
            payload["candidates"],
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )
        self.assertEqual(payload["recipes"], [])
        self.assertFalse(payload["image_requested"])

    def test_verify_route_keeps_ack_turn_for_candidate_re_registration(self):
        state = {
            "messages": self._messages("xing"),
            "verify_status": "ok",
            "verify_violated": [],
        }
        self.assertEqual(g.verify_route(state), "ok")


class RoutePickedCandidateTest(unittest.TestCase):
    """路由层解析「就第2个」：拿到菜名才算选定"""

    def test_resolve_from_store(self):
        with patch(
            "storage.sessions.find_recent_candidates",
            return_value={"record_id": 7, "candidates": ["鸡胸肉炒青菜", "青菜豆腐汤"]},
        ):
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "就第2个"), "青菜豆腐汤"
            )
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "第一道"), "鸡胸肉炒青菜"
            )

    def test_resolve_none_cases(self):
        with patch("storage.sessions.find_recent_candidates", return_value=None):
            self.assertIsNone(chat_route._resolve_picked_candidate("s1", "就第2个"))
        with patch(
            "storage.sessions.find_recent_candidates",
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
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=list(allergens or [])),
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

    def test_complete_candidate_list_skips_structured_llm(self):
        """正文清单已完整时直接登记候选，不再进入卡片结构化链。"""
        with (
            patch("agent.graph.build_structured_answer") as build_mock,
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(self._state(CANDIDATE_LIST_TEXT))

        build_mock.assert_not_called()
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(
            payload["candidates"],
            ["鸡胸肉炒青菜", "青菜豆腐汤", "鸡胸肉蔬菜沙拉"],
        )
        self.assertEqual(payload["recipes"], [])

    def test_incomplete_candidate_list_keeps_structured_fallback(self):
        """正文不足两道时仍保留结构化回退，避免漏掉模型后续给出的候选。"""
        opening = "1. 鸡胸肉炒青菜 —— 高蛋白低脂，10分钟出锅"
        answer = _chef_answer(["鸡胸肉炒青菜", "青菜豆腐汤"])
        with (
            patch("agent.graph.build_structured_answer", return_value=answer) as build_mock,
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._wants_multiple_recipes", return_value=True),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(self._state(opening))

        build_mock.assert_called_once_with("上下文")
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["answer_kind"], "candidates")
        self.assertEqual(payload["candidates"], ["鸡胸肉炒青菜", "青菜豆腐汤"])
        self.assertEqual(payload["recipes"], [])

    def test_candidate_payload_survives_structure_parse_failure(self):
        """正文已展示编号候选时，结构化菜谱链解析失败不能吞掉候选锚点。

        真实 T1 回归：模型正文正确列出三道候选，但结构化链连续 parse-fail，
        structure_answer_node 直接从异常分支返回空消息，checkpoint 里没有 candidates。
        下一轮「选第2个」因此找不到锚点，最终无回答、无卡片、无配图。
        """
        with (
            patch(
                "agent.graph.build_structured_answer",
                side_effect=ValueError("structured parse failed"),
            ),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
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
            "agent.graph.audit_allergens",
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
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["番茄鸡蛋汤"])) as build_mock,
            patch("agent.graph._build_structure_context",
                  return_value=(conclusion_context, None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._family_members", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

        build_mock.assert_called_once_with(conclusion_context)
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual([recipe["name"] for recipe in payload["recipes"]], ["番茄鸡蛋汤"])


class StructureAnswerSpecificDishRegressionTest(unittest.TestCase):
    """具体菜品请求必须进入单卡片链路，异常时也要留下可定位日志。"""

    def test_xihu_fish_howto_keeps_recipe_and_image_intent(self):
        text = "西湖醋鱼怎么做"
        messages = [
            HumanMessage(content=text),
            AIMessage(content="西湖醋鱼要用鱼、姜、醋和少量糖来调味。"),
        ]
        self.assertTrue(g.is_specific_dish_request(text))
        self.assertFalse(g.is_non_recipe_information_request(text))
        self.assertFalse(g._is_candidate_turn(messages))
        self.assertTrue(g._wants_recipe_images(messages))

        answer = _chef_answer(["西湖醋鱼"])
        with (
            patch("agent.graph.build_structured_answer", return_value=answer) as build_mock,
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

        build_mock.assert_called_once()
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual([recipe["name"] for recipe in payload["recipes"]], ["西湖醋鱼"])
        self.assertTrue(payload["image_requested"])
        self.assertIsNone(payload["recipes"][0]["image_url"])

    def test_structured_exception_is_logged_without_changing_fallback(self):
        messages = [
            HumanMessage(content="西湖醋鱼怎么做"),
            AIMessage(content="西湖醋鱼要用鱼、姜、醋和少量糖来调味。"),
        ]
        with (
            patch(
                "agent.graph.build_structured_answer",
                side_effect=RuntimeError("structured provider unavailable"),
            ),
            patch("agent.graph._allergens_for_audit", return_value=[]),
            self.assertLogs("agent.graph", level="ERROR") as captured,
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

        self.assertEqual(result, {"messages": []})
        log_text = "\n".join(captured.output)
        self.assertIn("structure_answer_node failed", log_text)
        self.assertIn("exc_type=RuntimeError", log_text)
        self.assertIn("latest_text='西湖醋鱼怎么做'", log_text)
        self.assertIn("is_candidate_turn=False", log_text)
        self.assertIn("wants_images=True", log_text)


class StructureAnswerSodiumLightTest(unittest.TestCase):
    """单菜单卡片的钠灯必须有确定性兜底，但不覆盖模型已有结论。"""

    def _messages(self):
        return [
            HumanMessage(content="请给我推荐一道清蒸鲈鱼"),
            AIMessage(content="清蒸鲈鱼的关键是控制蒸制时间，出锅后少量调味。"),
        ]

    def _answer(self, health_lights=None):
        return ChefAnswer(
            recipes=[
                Recipe(
                    name="清蒸鲈鱼",
                    intro="鲜嫩少油",
                    difficulty=2,
                    nutrition=4,
                    seasonings=[Seasoning(name="蒸鱼豉油", amount="2勺")],
                    steps=["鱼蒸熟后淋入蒸鱼豉油。"],
                )
            ],
            health_lights=list(health_lights or []),
            chef_tip="",
        )

    def _run(self, answer):
        with (
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": self._messages(),
                "verify_status": "ok",
                "verify_violated": [],
            })
        return json.loads(result["messages"][-1].content)

    def test_missing_sodium_light_is_backfilled_from_seasoning(self):
        payload = self._run(self._answer())
        sodium = [light for light in payload["health_lights"] if light["label"] == "钠"]
        self.assertEqual(len(sodium), 1)
        self.assertEqual(sodium[0]["level"], "yellow")
        self.assertIn("蒸鱼豉油", sodium[0]["reason"])

    def test_existing_sodium_light_is_not_overwritten(self):
        existing = HealthLight(label="钠", level="red", reason="模型已有的高风险结论")
        payload = self._run(self._answer([existing]))
        sodium = [light for light in payload["health_lights"] if light["label"] == "钠"]
        self.assertEqual(len(sodium), 1)
        self.assertEqual(sodium[0]["level"], "red")
        self.assertEqual(sodium[0]["reason"], "模型已有的高风险结论")


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

    def test_explicit_recipe_detail_request_restores_structured_route(self):
        messages = [
            HumanMessage(content="我有鸡胸肉和青菜"),
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤"]),
                ensure_ascii=False,
            )),
            HumanMessage(content=(
                "【已选定候选：鸡胸肉炒青菜】\n"
                "用1吧，父亲高血压，少放盐。请再给我完整做法和食材用量"
            )),
            AIMessage(content="要不要我再帮你调整成更清淡的版本？"),
        ]
        request = "用1吧，父亲高血压，少放盐。请再给我完整做法和食材用量"
        self.assertTrue(g._is_explicit_recipe_detail_request(request))
        self.assertEqual(
            g.verify_route({
                "verify_status": "degraded",
                "messages": messages,
            }),
            "degraded",
        )

    def test_plain_taste_followup_remains_plain(self):
        request = "这道菜少放一点盐，可以吗？"
        self.assertFalse(g._is_explicit_recipe_detail_request(request))

    def test_ingredient_howto_still_requires_candidate_selection(self):
        request = "鸡胸肉怎么做"
        self.assertFalse(g._is_explicit_recipe_detail_request(request))
        self.assertFalse(g._is_recipe_detail_followup(
            [HumanMessage(content=request)],
            request,
        ))


class DeliveredCardFollowupGuardTest(unittest.TestCase):
    """卡片交付后的普通追问只回文字：不重出卡片、不补图（技能验收场景 5）

    回归背景（2026-10-15 实测 user_d2cc49f12c / user_c5d8dcc6b7）：卡片已经交付后，
    下面这些轮次被判成 followup/other 却直落结构化链，重出一张几乎相同的卡片且
    `image_requested=False`——用户既丢了图，也分不清是上一道菜的补充还是新一轮。
    """

    FOLLOWUPS = (
        "这道菜大概要炖多久？",
        "我妈妈在控糖，我爸爸没有，今晚做两个人的饭",
        "你好，你能做什么",
        "做的时候需要焯水吗",
    )

    def _messages(self, user_text, names=("减盐减油版红烧肉",)):
        """真实状态形态：用户消息之后还有 chef_think 流式产出的 AIMessage。

        漏掉末尾这条 AIMessage 会改变闸门判定（`opening` 为空、候选兜底拿不到清单），
        测的就不是线上那条链路了。
        """
        return [
            HumanMessage(content="推荐几道菜"),
            AIMessage(content=json.dumps(
                _candidates_payload(list(names)), ensure_ascii=False)),
            HumanMessage(content="就第1个"),
            AIMessage(content=json.dumps(
                _delivered_card(list(names)), ensure_ascii=False)),
            HumanMessage(content=user_text),
            AIMessage(content="好的，我按这道菜的情况说清楚。"),
        ]

    def _run(self, messages, answer=None):
        with (
            patch("agent.graph.build_structured_answer",
                  return_value=answer or _chef_answer(["减盐减油版红烧肉"])) as mocked,
            patch("agent.graph._build_structure_context",
                  return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })
        return result, mocked

    def test_plain_followups_after_card_emit_no_message(self):
        for text in self.FOLLOWUPS:
            with self.subTest(text=text):
                result, mocked = self._run(self._messages(text))
                self.assertEqual(result, {"messages": []})
                mocked.assert_not_called()

    def test_blocked_followups_are_reported_as_non_structured_turns(self):
        """被挡下的轮次必须识别成 followup/other，而不是「选定/换菜」。"""
        for text in self.FOLLOWUPS:
            with self.subTest(text=text):
                messages = self._messages(text)
                self.assertIn(g._classify_turn_intent(messages), ("followup", "other"))
                self.assertFalse(g._is_dish_pick_turn(messages))
                self.assertFalse(g._wants_another_dish(text))

    def test_no_card_yet_still_structures(self):
        """没有交付过卡片时，同样一句话不该被这条闸门吃掉（闸门只认已交付卡片）。"""
        messages = [
            HumanMessage(content="我有鸡胸肉和青菜"),
            AIMessage(content="还有别的想法可以告诉我。"),
            HumanMessage(content="这道菜大概要炖多久？"),
        ]
        result, mocked = self._run(messages)
        self.assertNotEqual(result, {"messages": []})
        mocked.assert_called_once()

    def test_new_dish_requests_after_card_are_not_blocked_by_the_guard(self):
        """「还要别的菜」是新一轮索取，不能被闸门静默掉。

        闸门管的就是「有没有进结构化链」，所以判据是 build_structured_answer 被调用；
        「再推荐一道 / 换一道菜」被判成候选轮后由既有候选分支收口（正文清单不足两道时
        本来就不出卡片），那是基线行为，不由本闸门负责。
        """
        for text in ("还有别的菜吗", "再推荐一道", "换一道菜"):
            with self.subTest(text=text):
                _, mocked = self._run(self._messages(text))
                mocked.assert_called_once()

    def test_named_dish_after_card_still_emits_card(self):
        result, mocked = self._run(self._messages("我想吃番茄炒蛋"))
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual([item["name"] for item in payload["recipes"]],
                         ["减盐减油版红烧肉"])
        mocked.assert_called_once()

    def test_explicit_detail_request_after_card_is_not_blocked_by_the_guard(self):
        _, mocked = self._run(self._messages("请再给我完整做法和食材用量"))
        mocked.assert_called_once()

    def test_taste_adjustment_after_card_still_structures(self):
        result, mocked = self._run(
            self._messages("就吃这道菜品，想做道更辣些的")
        )
        self.assertNotEqual(result, {"messages": []})
        mocked.assert_called_once()

    def test_wants_another_dish_markers(self):
        for text in ("还有别的菜吗", "再推荐一道", "换个菜吧", "有没有其它的菜"):
            with self.subTest(text=text):
                self.assertTrue(g._wants_another_dish(text))
        for text in ("", "这道菜大概要炖多久？", "盐少放一点", "爸爸高血压能吃吗"):
            with self.subTest(text=text):
                self.assertFalse(g._wants_another_dish(text))


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

    def test_night_snack_drinks_shop_choice_is_restaurant(self):
        text = "那如果我要去吃夜宵并且再配酒的话，并且4个人，推荐去哪一家店"
        self.assertTrue(g._is_restaurant_ordering_scene(text))
        self.assertTrue(chat_route._is_restaurant_ordering_scene(text))
        self.assertEqual(
            g._classify_turn_intent([HumanMessage(content=text)]),
            "restaurant",
        )
        self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
        self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_food_occasion_without_place_stays_recipe_recommendation(self):
        for text in ["夜宵推荐几道菜", "配酒推荐几道菜"]:
            with self.subTest(text=text):
                self.assertFalse(g._is_restaurant_ordering_scene(text))
                self.assertNotEqual(
                    g._classify_turn_intent([HumanMessage(content=text)]),
                    "restaurant",
                )

    def test_restaurant_switch_requires_prior_restaurant_turn(self):
        text = "还是换一家吧"
        self.assertFalse(g._is_restaurant_ordering_scene(text))
        self.assertNotEqual(
            g._classify_turn_intent([HumanMessage(content=text)]),
            "restaurant",
        )
        messages = [
            HumanMessage(
                content="那如果我要去吃夜宵并且再配酒的话，并且4个人，推荐去哪一家店"
            ),
            AIMessage(content="可以，我来帮你找附近的夜宵店。"),
            HumanMessage(content=text),
        ]
        self.assertTrue(g._has_prior_restaurant_context(messages))
        self.assertEqual(g._classify_turn_intent(messages), "restaurant")

    def test_consecutive_restaurant_switches_keep_scene(self):
        messages = [
            HumanMessage(
                content="那如果我要去吃夜宵并且再配酒的话，并且4个人，推荐去哪一家店"
            ),
            AIMessage(content="可以，我来帮你找附近的夜宵店。"),
            HumanMessage(content="换一家"),
            AIMessage(content="好，我再换一批店。"),
            HumanMessage(content="还是换一家吧"),
        ]
        self.assertTrue(g._has_prior_restaurant_context(messages))
        self.assertEqual(g._classify_turn_intent(messages), "restaurant")

    def test_change_one_is_not_restaurant_switch(self):
        self.assertEqual(
            g._classify_turn_intent([HumanMessage(content="换一道")]),
            "change_one",
        )

    def test_restaurant_turn_skips_structured_card(self):
        state = {
            "messages": [
                HumanMessage(
                    content="那如果我要去吃夜宵并且再配酒的话，并且4个人，推荐去哪一家店"
                ),
                AIMessage(content="可以，我来帮你找附近的夜宵店。"),
            ],
            "verify_status": "ok",
            "verify_violated": [],
        }
        with patch.object(g, "build_structured_answer") as mocked:
            self.assertEqual(g.structure_answer_node(state), {"messages": []})
        mocked.assert_not_called()

    def test_restaurant_turn_gets_external_dining_prompt(self):
        text = "那如果我要去吃夜宵并且再配酒的话，并且4个人，推荐去哪一家店"
        with patch("agent.graph.llm_with_tools") as mocked_llm:
            g.chef_agent_node({"messages": [HumanMessage(content=text)]})
        payload = mocked_llm.invoke.call_args.args[0]
        system_text = str(payload[0].content)
        self.assertIn("外食找店规则", system_text)
        self.assertIn("nearby_food", system_text)
        self.assertNotIn("默认单菜规则", system_text)


class ImagePipelineGateTest(unittest.TestCase):
    """配图门两层同源：候选阶段不烧图"""

    def test_generic_recommend_no_image(self):
        for text in [
            "我有鸡胸肉和青菜",
            "推荐几道菜",
            "不知道吃什么",
            "吃麻薯的话有几种吃法",
            "麻薯有哪些做法",
        ]:
            with self.subTest(text=text):
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
                self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_explanation_question_no_image(self):
        for text in [
            "这道菜为什么要先炒蛋",
            "这道菜适合谁吃",
            "这个菜谱为什么只要三步",
            "前面那个步骤没看懂，解释一下",
        ]:
            with self.subTest(text=text):
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
                self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_named_dish_keeps_image(self):
        for text in ["我想吃番茄炒蛋", "就做番茄炒蛋"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._should_enable_image_pipeline(text, None))
                self.assertTrue(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_change_one_without_named_dish_stays_before_image_stage(self):
        text = "没胃口换一道"
        self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
        self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))

    def test_generic_recommend_with_image_does_not_bypass_candidates(self):
        text = "推荐几道菜，配张图"
        self.assertFalse(chat_route._should_enable_image_pipeline(text, None))
        self.assertFalse(g._wants_recipe_images([HumanMessage(content=text)]))
        self.assertTrue(g._is_candidate_turn([HumanMessage(content=text)]))

    def test_image_action_phrases_are_recognized(self):
        for text in ["配张图片", "我要图片", "要图片", "配图片", "配一张图", "配个图"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._wants_image(text, None))

    def test_contextual_image_phrase_is_image_request_not_change(self):
        """「给这道菜配一张图」这类指代当前菜的配图话必须认成配图意图。

        复现证据：`sessions/user_b67ee4024f.json` 第 2 轮选定「西红柿鸡蛋汤」带图，
        第 3 轮「给这道菜配一张图」此前 `_wants_image` 判 False → 不进就地补图链路，
        反而落回通用 Agent，模型把菜名换成「番茄青椒蛋花汤」且没有配图。
        指代当前菜，所以不解析成点名新菜（`_extract_requested_dish` 为 None），
        补图对象交给会话里的最近一张卡片。
        """
        for text in ["给这道菜配一张图", "这道菜配一张图", "再配一张图"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._wants_image(text, None))
                self.assertFalse(chat_route._is_recipe_change_request(text))
                self.assertIsNone(chat_route._extract_requested_dish(text))

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

    def test_pure_image_request_opens_image_pipeline(self):
        """「要图片的」这类纯配图请求承接上一轮菜品，必须真的开配图链路。

        回归证据：`sessions/user_cd56c7d003.json` 第 2 轮 —— 上一轮被过敏原拦截
        （recipes 为空数组、没有可补图的卡片），用户回「要图片的」，此前
        `_should_enable_image_pipeline` 返回 False，模型于是端出一道**没有图**
        的成品卡。现在这一轮必须授权配图，最终仍以 recipes 为准。
        """
        for text in ["要图片的", "配张图", "配张图片", "我要图片", "给我来张成品图"]:
            with self.subTest(text=text):
                self.assertTrue(chat_route._should_enable_image_pipeline(text, None))
                self.assertTrue(
                    g._wants_recipe_images(
                        [HumanMessage(content=f"【配图开关：开启】\n{text}")]
                    )
                )

    def test_pure_image_request_does_not_swallow_other_demands(self):
        """纯配图判据不能把泛推荐/换菜里夹带的「要图」也算成纯配图请求。"""
        for text in [
            "推荐几道菜，配张图",
            "换一道清淡的，要图片的",
            "顺便配张图，说说营养",
        ]:
            with self.subTest(text=text):
                self.assertFalse(chat_route._is_pure_image_request(text, None))
                self.assertFalse(chat_route._should_enable_image_pipeline(text, None))


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


class NonRecipeInformationTurnTest(unittest.TestCase):
    """食材处理、安全、营养和比较问题只走文字，不进入菜谱结构化。"""

    CASES = [
        "虾怎么处理",
        "牛肉怎么腌",
        "糯米粉怎么挑",
        "麻薯怎么保存",
        "虾和什么不能一起吃",
        "吃麻薯会胖吗",
        "番茄炒蛋和红烧肉哪个更健康",
        "吃火锅的时候喝什么",
        "你刚才说的放盐是什么意思",
        "这道菜为什么要先炒蛋",
        "为什么焯水这一步不能省",
        "这段讲解里说的火候是什么意思",
        "这道菜适合谁吃",
        "这个菜谱为什么只要三步",
        "前面那个步骤没看懂，解释一下",
        "先告诉我原理，再决定做不做",
        "解释一下番茄炒蛋为什么要先炒蛋",
        "麻薯为什么不能放冰箱",
    ]

    def test_information_question_skips_structure(self):
        for text in self.CASES:
            with self.subTest(text=text), patch.object(
                g, "build_structured_answer"
            ) as mocked:
                state = {
                    "messages": [
                        HumanMessage(content=text),
                        AIMessage(content="这是对问题的文字说明。"),
                    ],
                    "verify_status": "ok",
                    "verify_violated": [],
                }
                self.assertEqual(g.structure_answer_node(state), {"messages": []})
                mocked.assert_not_called()

    def test_information_question_routes_plain(self):
        for text in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(
                    g.verify_route(
                        {
                            "messages": [
                                HumanMessage(content=text),
                                AIMessage(content="这是对问题的文字说明。"),
                            ],
                            "verify_status": "ok",
                            "verify_violated": [],
                        }
                    ),
                    "plain",
                )


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

    def test_find_crosses_consumed_card_back_to_its_list(self):
        """卡片是「上一份清单被选定后消费出来的」时，序号锚点仍指向那份清单。

        实测 user_777df48ed8：5 道候选 → 用户选第 5 道出卡片 → 下一轮「还是第 1 道」。
        旧实现只读最后一条记录（卡片，无 candidates）→ 返回 None → 路由层不注入菜名
        → 模型自由发挥静默换了鱼种（香煎龙利鱼 → 清蒸鲈鱼）。
        """
        self.session["messages"] = [
            {
                "id": 1,
                "user_text": "继续推荐几道菜品",
                "candidates": ["香煎龙利鱼", "番茄炖豆腐", "白灼芥蓝"],
            },
            {
                "id": 2,
                "user_text": "那就第3道菜品吧，可以甜一点",
                "answer": json.dumps(
                    {"recipes": [{"name": "白灼芥蓝 · 微甜蒜香改版"}]},
                    ensure_ascii=False,
                ),
                "image_url": "http://x/y.png",
            },
        ]
        with self._patch():
            found = sessions_store.find_recent_candidates("s_cand")
        self.assertEqual(found["record_id"], 1)
        self.assertEqual(
            found["candidates"], ["香煎龙利鱼", "番茄炖豆腐", "白灼芥蓝"]
        )
        self.assertEqual(found["dish_name"], "香煎龙利鱼")

    def test_find_stops_at_card_that_did_not_come_from_the_list(self):
        """中间卡片跟上一份清单无关时，不许把旧清单当成实时候选。"""
        self.session["messages"] = [
            {
                "id": 1,
                "user_text": "推荐几道",
                "candidates": ["番茄炒蛋", "番茄鸡蛋汤"],
            },
            {
                "id": 2,
                "user_text": "我想吃土豆丝",
                "answer": json.dumps(
                    {"recipes": [{"name": "酸辣土豆丝"}]}, ensure_ascii=False
                ),
            },
        ]
        with self._patch():
            self.assertIsNone(sessions_store.find_recent_candidates("s_cand"))


class CandidateAnchorFallbackTest(unittest.TestCase):
    """结构事件缺失时，落库正文仍能建立候选锚点，且不误收普通菜谱步骤。"""

    def test_generic_recommend_text_registers_candidates(self):
        with patch("storage.sessions.set_message_candidates", return_value=True) as mocked:
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
        with patch("storage.sessions.set_message_candidates", return_value=True) as mocked:
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

    def test_prefixed_bare_number_with_particle(self):
        """「那就3吧」这类承接前缀 + 裸序号（实测 user_c5d8dcc6b7 第 2 轮的根因）

        用户承接候选清单时先说「那就」，前缀把裸序号形态整体吃掉，
        序号解析不出来 → 配图门开不了、已选定菜名也没注入 → 有卡无图。
        """
        for text, expected in [
            ("那就3吧", 3),
            ("那就要2吧", 2),
            ("那就选3吧", 3),
            ("那选2吧", 2),
            ("那就3", 3),
            ("那就 3 吧，少盐一点", 3),
            ("那就二吧", 2),
        ]:
            with self.subTest(text=text):
                self.assertEqual(g.parse_candidate_index(text), expected)

    def test_prefixed_bare_number_without_particle_is_not_index(self):
        # 带量词/名词的仍然不能当序号：「那就2个菜」「那就3个人」「那就是3个人吧」
        for text in ["那就2个菜", "那就3个人", "那就是3个人吧", "那就一份吧", "那就先这样吧"]:
            with self.subTest(text=text):
                self.assertIsNone(g.parse_candidate_index(text))

    def test_route_layer_resolves_bare_number_pick(self):
        with patch(
            "storage.sessions.find_recent_candidates",
            return_value={"record_id": 7, "candidates": ["鸡胸肉炒青菜", "青菜豆腐汤", "番茄牛腩"]},
        ):
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "3"),
                "番茄牛腩",
            )
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "3吧，但是可以辣一点"),
                "番茄牛腩",
            )
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "选2吧"),
                "青菜豆腐汤",
            )
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "做3把"),
                "番茄牛腩",
            )
            # 承接前缀形态：路由层拿到的就是用户原话「那就3吧」
            self.assertEqual(
                chat_route._resolve_picked_candidate("s1", "那就3吧"),
                "番茄牛腩",
            )

    def test_agent_layer_resolves_selection_command_with_particle(self):
        msgs = [
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤", "番茄牛腩"]),
                ensure_ascii=False,
            )),
            HumanMessage(content="选2吧"),
        ]
        self.assertEqual(g.resolve_candidate_pick(msgs), (2, "青菜豆腐汤"))
        self.assertTrue(g._is_dish_pick_turn(msgs))

        msgs[-1] = HumanMessage(content="做3把")
        self.assertEqual(g.resolve_candidate_pick(msgs), (3, "番茄牛腩"))
        self.assertTrue(g._is_dish_pick_turn(msgs))

    def test_simple_pick_keeps_full_opening_and_uses_model(self):
        msgs = [
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤", "番茄牛腩"]),
                ensure_ascii=False,
            )),
            HumanMessage(content=(
                "【已选定候选：青菜豆腐汤】\n"
                "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开，"
                "不得改名、不得替换成别的菜。）\n\n"
                "【配图开关：开启】\n"
                "选2吧"
            )),
        ]
        with patch("agent.graph.llm_with_tools") as mocked_llm:
            mocked_llm.invoke.return_value = AIMessage(
                content="好的，已按你选的第2道「青菜豆腐汤」整理完整做法。"
            )
            result = g.chef_agent_node({"messages": msgs})

        mocked_llm.invoke.assert_called_once()
        reply = str(result["messages"][0].content)
        self.assertEqual(reply, "好的，已按你选的第2道「青菜豆腐汤」整理完整做法。")

    def test_pick_with_extra_adjustment_still_uses_model(self):
        msgs = [
            AIMessage(content=json.dumps(
                _candidates_payload(["鸡胸肉炒青菜", "青菜豆腐汤", "番茄牛腩"]),
                ensure_ascii=False,
            )),
            HumanMessage(content=(
                "【已选定候选：青菜豆腐汤】\n"
                "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开，"
                "不得改名、不得替换成别的菜。）\n\n"
                "选2吧，可以辣一点"
            )),
        ]
        fake = AIMessage(content="好的，按你选的青菜豆腐汤，把辣度再提高一点。")
        with patch("agent.graph.llm_with_tools") as mocked_llm:
            mocked_llm.invoke.return_value = fake
            result = g.chef_agent_node({"messages": msgs})

        mocked_llm.invoke.assert_called_once()
        self.assertEqual(result["messages"][0].content, fake.content)


class TasteAdjustmentFollowupTest(unittest.TestCase):
    """已交付单菜卡片后的口感追轮必须锁定原菜并更新卡片与配图。"""

    DISH = "青菜炒鸡蛋"

    def _messages(self, *, with_opening=False):
        messages = [
            HumanMessage(content="选2吧"),
            AIMessage(content=json.dumps({
                "opening": "好的，已按你选的第2道「青菜炒鸡蛋」整理完整做法。",
                "answer_kind": "recipe",
                "recipes": [{
                    "name": self.DISH,
                    "intro": "青菜脆嫩、鸡蛋蓬松",
                    "steps": ["鸡蛋中火炒至刚凝固", "青菜大火快炒后回锅"],
                    "image_url": "https://images.test/qingcai.png",
                    "image_ai_generated": True,
                }],
            }, ensure_ascii=False)),
            HumanMessage(content="再改善一下口感"),
        ]
        if with_opening:
            messages.append(AIMessage(content="可以把鸡蛋炒得更嫩，青菜最后大火快炒。"))
        return messages

    def test_followup_target_is_the_latest_single_card(self):
        self.assertEqual(
            g._taste_adjustment_followup_target(self._messages()),
            self.DISH,
        )
        self.assertTrue(g._wants_recipe_images(self._messages()))

    def test_taste_praise_does_not_become_adjustment_followup(self):
        messages = self._messages()
        messages[-1] = HumanMessage(content="感觉这道会更好吃")
        self.assertFalse(g._is_taste_adjustment_request("感觉这道会更好吃"))
        self.assertEqual(g._taste_adjustment_followup_target(messages), "")

    def test_followup_uses_plain_llm_and_pins_dish(self):
        fake = AIMessage(content="保持青菜炒鸡蛋不变，把鸡蛋炒得更嫩一些。")
        with (
            patch("agent.graph.llm") as plain_llm,
            patch("agent.graph.summary_llm") as summary_llm,
            patch("agent.graph.llm_with_tools") as tool_llm,
        ):
            summary_llm.invoke.return_value = fake
            result = g.chef_agent_node({"messages": self._messages()})

        tool_llm.invoke.assert_not_called()
        plain_llm.invoke.assert_not_called()
        summary_llm.invoke.assert_called_once()
        system_text = str(summary_llm.invoke.call_args.args[0][0].content)
        self.assertIn(self.DISH, system_text)
        self.assertIn("禁止换成别的菜", system_text)
        self.assertIn("不要调用工具", system_text)
        self.assertIn("完整可执行做法", system_text)
        self.assertIn("更新原菜谱卡片", system_text)
        self.assertEqual(result["messages"], [fake])

    def test_followup_builds_updated_single_card(self):
        with (
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["模型误命名的菜"])) as build_mock,
            patch("agent.graph._build_structure_context",
                  return_value=("调整后的完整做法", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": self._messages(with_opening=True),
                "verify_status": "ok",
                "verify_violated": [],
            })

        build_mock.assert_called_once_with("调整后的完整做法")
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["recipes"][0]["name"], self.DISH)
        self.assertTrue(payload["image_requested"])

    def test_followup_question_still_builds_updated_card(self):
        messages = self._messages(with_opening=True)
        messages[-1] = AIMessage(content="这样调整后会更清淡，你觉得可以吗？")
        with (
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["模型误命名的菜"])),
            patch("agent.graph._build_structure_context",
                  return_value=("调整后的完整做法", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })

        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["recipes"][0]["name"], self.DISH)


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
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["青菜豆腐汤"])) as mocked_build,
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
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
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["青菜豆腐汤"])) as mocked_build,
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
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
        with patch("agent.graph.llm_with_tools") as mocked_llm:
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
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["甜辣拌内酯豆腐"])),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
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
            patch("agent.graph.build_structured_answer",
                  return_value=_chef_answer(["柠檬米醋蒸鲈鱼（低温·零含钠调味版）"])) as mocked_build,
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
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


class RetryToolSlotTest(unittest.TestCase):
    """verify 判 retry 后必须至少留一次取证，否则重试必然被兜底句顶掉（P1）。

    实测链路（2026-10-09，选定轮「2吧…我的父亲高血压，可以吃这个吗」）：
    本轮并发宽度被收紧到 `PICK_TURN_TOOL_BUDGET=2`，且已被 web_search +
    nutrition_kb_search 并排用光；正文里真写了「生抽2大勺、盐5g」→ verify 判 retry →
    chef_think 再要工具时余额为 0 → 路由判 `tool_budget_exhausted` →
    `tool_budget_finalize` 把用户已经看到的好答案替换成
    「这次没查到足够具体的做法…」。所以回炉时必须补回一次取证额度。

    边界：只补「一次」，`MAX_TOOL_ROUNDS` / `MAX_VERIFY` 的封顶一个都不能绕过。
    """

    ANSWER = "番茄牛肉面：面条煮熟，加生抽2大勺、盐5g调味，铺上牛肉即可。"
    # 重试后模型并排提 3 个：补的额度只该放行 1 个，不是敞开
    RETRY_CALLS = [
        {"id": "retry-t1", "name": "nutrition_kb_search", "args": {"query": "低钠 面食"}},
        {"id": "retry-t2", "name": "web_search", "args": {"query": "少盐 面条"}},
        {"id": "retry-t3", "name": "nutrition_kb_search", "args": {"query": "高血压 外卖"}},
    ]

    def _state(self, **overrides):
        state = {
            "messages": list(add_messages([], [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["番茄牛肉面", "蒜香鸡胸"]), ensure_ascii=False)),
                HumanMessage(content="2吧，我的父亲高血压，可以吃这个吗，要吃的话该注意些什么"),
                AIMessage(content=self.ANSWER),
            ])),
            "verify_attempts": 0,
            "tool_calls_in_turn": g.PICK_TURN_TOOL_BUDGET,
            "tool_budget": g.PICK_TURN_TOOL_BUDGET,
            "tool_rounds": 1,
            "tool_budget_exhausted": False,
        }
        state.update(overrides)
        return state

    def _retry_then_ask_tools(self, state):
        """跑 verify 拿到 retry，把补丁并回 state，再让 chef_think「重试后又要工具」。"""
        out = g.verify_answer_node(state)
        self.assertEqual(out["verify_status"], "retry")
        merged = {**state, **{k: v for k, v in out.items() if k != "messages"}}
        merged["messages"] = add_messages(state["messages"], out.get("messages") or [])
        merged["messages"] = add_messages(
            merged["messages"], [AIMessage(content="", tool_calls=self.RETRY_CALLS)]
        )
        return merged

    def test_retry_grants_one_slot_when_balance_empty(self):
        merged = self._retry_then_ask_tools(self._state())
        self.assertEqual(g._remaining_tool_slots(merged), 1)
        self.assertEqual(g.chef_route_with_tool_budget(merged), "tools")

    def test_retry_leaves_state_alone_when_balance_left(self):
        # 余额还有剩就不许动计数，避免每次重试都无谓放宽额度
        out = g.verify_answer_node(self._state(tool_calls_in_turn=0, tool_rounds=0))
        self.assertEqual(out["verify_status"], "retry")
        self.assertNotIn("tool_calls_in_turn", out)
        self.assertNotIn("tool_rounds", out)

    def test_retry_grants_one_round_when_round_cap_hit(self):
        # 余额还有 1 但圈数已封顶：同样要留一次取证，否则照样落兜底句
        merged = self._retry_then_ask_tools(self._state(
            tool_calls_in_turn=g.MAX_TOOL_CALLS_PER_TURN - 1,
            tool_budget=g.MAX_TOOL_CALLS_PER_TURN,
            tool_rounds=g.MAX_TOOL_ROUNDS,
        ))
        self.assertEqual(merged["tool_rounds"], g.MAX_TOOL_ROUNDS - 1)
        self.assertEqual(g.chef_route_with_tool_budget(merged), "tools")

    def test_retry_slot_executes_only_one_tool(self):
        class _FakeExecutor:
            def __init__(self):
                self.seen_ids = []

            def invoke(self, state):
                calls = list(getattr(state["messages"][-1], "tool_calls") or [])
                self.seen_ids = [c["id"] for c in calls]
                return {"messages": [
                    ToolMessage(content="{}", tool_call_id=c["id"], name=c["name"])
                    for c in calls
                ]}

        merged = self._retry_then_ask_tools(self._state())
        executor = _FakeExecutor()
        with patch.object(g, "tool_executor", executor):
            out = g.run_tools_node(merged)
        self.assertEqual(executor.seen_ids, ["retry-t1"])
        self.assertEqual(out["tool_calls_in_turn"], g.PICK_TURN_TOOL_BUDGET)

    def test_retry_does_not_bypass_round_cap(self):
        # 补的那一圈用掉后仍然收口：计数回到封顶值时路由依旧判耗尽
        merged = self._retry_then_ask_tools(self._state(
            tool_calls_in_turn=g.MAX_TOOL_CALLS_PER_TURN - 1,
            tool_budget=g.MAX_TOOL_CALLS_PER_TURN,
            tool_rounds=g.MAX_TOOL_ROUNDS,
        ))
        merged["tool_rounds"] = g.MAX_TOOL_ROUNDS
        merged["tool_calls_in_turn"] = g.MAX_TOOL_CALLS_PER_TURN
        self.assertEqual(
            g.chef_route_with_tool_budget(merged), "tool_budget_exhausted"
        )


class RetrySlotOncePerTurnTest(unittest.TestCase):
    """重试回路降温：同一轮第 2、3 次重试不再各开一圈新工具（P2）。

    证据：trace 里 `chef_think` 854 次 / 3362s 是最大头，usage 显示平均 5.73 次
    LLM 调用/轮、最高 10 次；`_retry_tool_slot_patch` 原本**每次**重试都补 1 格并发
    额度并回退 1 圈，于是 `MAX_VERIFY=3` 之外还能再叠最多 3 圈工具 + 3 次生成。
    这里收紧成「每轮只补一次」，与同文件 `_pick_turn_slot_patch` 的口径对齐。

    首次重试照旧补额（既有「不让好答案被兜底句顶掉」的保护一个不少）；
    被拒的第 2 次重试则走既有 `tool_budget_finalize` 收口，收口用本轮已取到的
    证据生成，不是静态兜底句；过敏原/慢病的安全判定与路由完全不变。
    """

    ANSWER = "番茄牛肉面：面条煮熟，加生抽2大勺、盐5g调味，铺上牛肉即可。"
    RETRY_CALLS = [
        {"id": "retry-t1", "name": "nutrition_kb_search", "args": {"query": "低钠 面食"}},
    ]

    def _state(self, **overrides):
        state = {
            "messages": list(add_messages([], [
                HumanMessage(content="我有鸡胸肉和青菜"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["番茄牛肉面", "蒜香鸡胸"]), ensure_ascii=False)),
                HumanMessage(content="2吧，我的父亲高血压，可以吃这个吗"),
                AIMessage(content=self.ANSWER),
            ])),
            "verify_attempts": 0,
            "tool_calls_in_turn": g.PICK_TURN_TOOL_BUDGET,
            "tool_budget": g.PICK_TURN_TOOL_BUDGET,
            "tool_rounds": 1,
            "tool_budget_exhausted": False,
            "retry_slot_granted": False,
        }
        state.update(overrides)
        return state

    @staticmethod
    def _merge(state, out):
        merged = {**state, **{k: v for k, v in out.items() if k != "messages"}}
        merged["messages"] = add_messages(state["messages"], out.get("messages") or [])
        return merged

    def test_first_retry_still_grants_and_marks_once(self):
        out = g.verify_answer_node(self._state())
        self.assertEqual(out["verify_status"], "retry")
        self.assertEqual(out["tool_calls_in_turn"], g.PICK_TURN_TOOL_BUDGET - 1)
        self.assertTrue(out["retry_slot_granted"])

    def test_second_retry_same_turn_does_not_grant_again(self):
        merged = self._merge(self._state(), g.verify_answer_node(self._state()))
        # 第一次补的那格已被取完（计数回到封顶）；第二次重试不许再补
        merged["tool_calls_in_turn"] = g.PICK_TURN_TOOL_BUDGET
        out = g.verify_answer_node(merged)
        self.assertEqual(out["verify_status"], "retry")
        self.assertNotIn("tool_calls_in_turn", out)
        self.assertNotIn("tool_rounds", out)
        self.assertNotIn("retry_slot_granted", out)

    def test_denied_second_retry_routes_to_evidence_backed_finalize(self):
        merged = self._merge(self._state(), g.verify_answer_node(self._state()))
        merged["tool_calls_in_turn"] = g.PICK_TURN_TOOL_BUDGET
        merged = self._merge(merged, g.verify_answer_node(merged))
        merged["messages"] = add_messages(
            merged["messages"], [AIMessage(content="", tool_calls=self.RETRY_CALLS)]
        )
        self.assertEqual(g._remaining_tool_slots(merged), 0)
        self.assertEqual(
            g.chef_route_with_tool_budget(merged), "tool_budget_exhausted"
        )

        merged["messages"] = add_messages(merged["messages"], [
            ToolMessage(
                content='{"text":"可用低钠生抽替代，钠约 300mg/份"}',
                name="nutrition_kb_search",
                tool_call_id="retry-t1",
            ),
        ])
        with patch("agent.graph.llm") as fake_llm, \
                patch("agent.graph._allergens_for_audit", lambda *a, **k: set()):
            fake_llm.invoke.return_value = AIMessage(
                content="换成低钠生抽的番茄牛肉面，少放盐。"
            )
            result = g.tool_budget_finalize_node(dict(merged))
        self.assertTrue(result["tool_budget_exhausted"])
        self.assertIn("低钠", result["messages"][-1].content)

    def test_grant_is_reset_each_turn(self):
        state = {
            "messages": list(add_messages([], [HumanMessage(content="新的一轮")])),
            "retry_slot_granted": True,
        }
        out = g.maybe_condense(state)
        self.assertFalse(out["retry_slot_granted"])


class PickTurnSecondRoundSlotTest(unittest.TestCase):
    """选定轮第二圈还要取证时必须补回一格额度，否则正文被兜底句顶掉（2026-10-11）。

    实测链路（session user_5294894983，「3」选定「鸡丝黄瓜凉拌面」）：第一圈并排搜 2 个
    （做法 + 依据）就用光 `PICK_TURN_TOOL_BUDGET=2`，第二圈模型继续取证 →
    `chef_route_with_tool_budget` 判 `tool_budget_exhausted` → `tool_budget_finalize`
    把正文换成「这次没查到足够具体的做法…」，卡片照出、讲解却是兜底句。

    边界：只补一格、每轮只补一次、仍受 `MAX_TOOL_ROUNDS` 封顶；非选定轮不补。
    """

    SECOND_ROUND_CALLS = [
        {"id": "s1", "name": "web_search", "args": {"query": "鸡丝凉面 少油少盐"}},
        {"id": "s2", "name": "nutrition_kb_search", "args": {"query": "高血压 凉面 钠"}},
    ]

    def _base_state(self, **overrides):
        """第二圈还没发生时的 state（chef_think 直接跑这一份）。"""
        state = {
            "messages": list(add_messages([], [
                HumanMessage(content="我有鸡胸肉、黄瓜和面条"),
                AIMessage(content=json.dumps(
                    _candidates_payload(["番茄鸡蛋面", "香菇青菜汤面", "鸡丝黄瓜凉拌面"]),
                    ensure_ascii=False,
                )),
                HumanMessage(content=(
                    "【已选定候选：鸡丝黄瓜凉拌面】\n"
                    "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开。）\n\n"
                    "3"
                )),
            ])),
            "tool_calls_in_turn": g.PICK_TURN_TOOL_BUDGET,
            "tool_budget": g.PICK_TURN_TOOL_BUDGET,
            "tool_rounds": 1,
            "pick_turn_slot_granted": False,
        }
        state.update(overrides)
        return state

    def _second_round(self):
        return AIMessage(content="", tool_calls=self.SECOND_ROUND_CALLS)

    def _state_after_second_round(self, **overrides):
        """第二圈「模型刚提出工具调用」时的 state（最后一条就是那个 tool_call 消息）。"""
        base = self._base_state(**overrides)
        base["messages"] = add_messages(base["messages"], [self._second_round()])
        return base

    class _FakeToolLLM:
        def __init__(self, response):
            self.response = response

        def invoke(self, payload):
            return self.response

    class _FakeExecutor:
        def __init__(self):
            self.seen_ids = []

        def invoke(self, state):
            calls = list(getattr(state["messages"][-1], "tool_calls") or [])
            self.seen_ids = [c["id"] for c in calls]
            return {"messages": [
                ToolMessage(content="{}", tool_call_id=c["id"], name=c["name"])
                for c in calls
            ]}

    def test_second_round_without_grant_is_judged_exhausted(self):
        # 现状复算：不补额度时第二圈被判预算耗尽 → 下一跳就是 tool_budget_finalize
        self.assertEqual(
            g.chef_route_with_tool_budget(self._state_after_second_round()),
            "tool_budget_exhausted",
        )

    def test_second_round_gets_one_more_slot(self):
        state = self._state_after_second_round()
        patch = g._pick_turn_slot_patch(state, self._second_round())
        self.assertEqual(patch["tool_calls_in_turn"], g.PICK_TURN_TOOL_BUDGET - 1)
        self.assertTrue(patch["pick_turn_slot_granted"])
        merged = {**state, **patch}
        self.assertEqual(g._remaining_tool_slots(merged), 1)
        self.assertEqual(g.chef_route_with_tool_budget(merged), "tools")

    def test_granted_slot_executes_only_one_tool(self):
        state = self._state_after_second_round()
        merged = {**state, **g._pick_turn_slot_patch(state, self._second_round())}
        executor = self._FakeExecutor()
        with patch.object(g, "tool_executor", executor):
            out = g.run_tools_node(merged)
        # 第二圈模型并排提 2 个，补的额度只放行 1 个
        self.assertEqual(executor.seen_ids, ["s1"])
        self.assertEqual(out["tool_calls_in_turn"], g.PICK_TURN_TOOL_BUDGET)

    def test_grant_happens_at_most_once_per_turn(self):
        state = self._state_after_second_round()
        merged = {**state, **g._pick_turn_slot_patch(state, self._second_round())}
        with patch.object(g, "tool_executor", self._FakeExecutor()):
            after_tools = g.run_tools_node(merged)
        merged = {
            **merged,
            "messages": add_messages(merged["messages"], after_tools["messages"]),
            "tool_calls_in_turn": after_tools["tool_calls_in_turn"],
            "tool_rounds": after_tools["tool_rounds"],
            "pick_turn_slot_granted": merged["pick_turn_slot_granted"],
        }
        # 补的那圈用掉后模型第三次还要工具：不再补，计数回到封顶值 → 照旧收口
        merged["messages"] = add_messages(merged["messages"], [self._second_round()])
        self.assertEqual(g._pick_turn_slot_patch(merged, self._second_round()), {})
        self.assertEqual(g.chef_route_with_tool_budget(merged), "tool_budget_exhausted")

    def test_non_pick_turn_is_not_granted(self):
        state = self._state_after_second_round(messages=list(add_messages([], [
            HumanMessage(content="帮我推荐几道明天中午带饭的菜"),
            self._second_round(),
        ])))
        self.assertEqual(g._pick_turn_slot_patch(state, self._second_round()), {})

    def test_first_round_with_balance_left_is_not_granted(self):
        # 第一圈余额还没用光就不许动计数，否则等于变相把选定轮宽度从 2 放宽
        state = self._state_after_second_round(tool_calls_in_turn=0, tool_rounds=0)
        self.assertEqual(g._remaining_tool_slots(state), g.PICK_TURN_TOOL_BUDGET)
        self.assertEqual(g._pick_turn_slot_patch(state, self._second_round()), {})

    def test_response_without_tool_calls_is_not_granted(self):
        state = self._base_state()
        self.assertEqual(
            g._pick_turn_slot_patch(state, AIMessage(content="这三道都在你的低盐护栏里。")),
            {},
        )

    def test_round_cap_still_binds(self):
        state = self._state_after_second_round(tool_rounds=g.MAX_TOOL_ROUNDS)
        self.assertEqual(g._pick_turn_slot_patch(state, self._second_round()), {})

    def test_chef_think_applies_grant_on_second_round(self):
        state = self._base_state()
        with patch.object(g, "llm_with_tools", self._FakeToolLLM(self._second_round())):
            out = g.chef_agent_node(state)
        self.assertEqual(out["tool_calls_in_turn"], g.PICK_TURN_TOOL_BUDGET - 1)
        self.assertTrue(out["pick_turn_slot_granted"])
        merged = {
            **state,
            "messages": add_messages(state["messages"], out["messages"]),
            "tool_calls_in_turn": out["tool_calls_in_turn"],
            "pick_turn_slot_granted": out["pick_turn_slot_granted"],
        }
        self.assertEqual(g.chef_route_with_tool_budget(merged), "tools")


class PickTurnCompiledGraphSlotTest(unittest.TestCase):
    """整图回归：选定轮补额度必须在**真实编译图**里生效，不能只在裸 dict 上成立。

    2026-10-12 根因：LangGraph 按节点函数首参的类型注解推断 input_schema，
    `chef_agent_node(state: MessagesState)` 让 chef_think 只收到 `{'messages'}`，
    于是 `_pick_turn_slot_patch` 读不到 `tool_budget / tool_calls_in_turn`，
    补额度分支恒不命中 → 第二圈被 `chef_route_with_tool_budget` 判预算耗尽 →
    用户已经看到的正文被 `tool_budget_finalize` 的兜底句顶掉。

    上面 `g.chef_agent_node({...})` 那批用例直传裸 dict，**绕过图裁剪**，测不到这个缺陷；
    这里补一条走整图的用例，首参注解再退化就会红。
    """

    CANDIDATES = ["番茄鸡蛋面", "香菇青菜汤面", "鸡丝黄瓜凉拌面"]
    OPENING = (
        "按你选的鸡丝黄瓜凉拌面来：鸡胸肉煮熟手撕，面条过凉水，"
        "生抽一勺、香醋两勺，拌匀即可。"
    )
    FALLBACK_MARK = "这次没查到足够具体的做法"

    ROUND1 = AIMessage(content="", tool_calls=[
        {"id": "ct1", "name": "web_search", "args": {"query": "鸡丝凉面 做法"}},
        {"id": "ct2", "name": "nutrition_kb_search", "args": {"query": "高血压 凉面 钠"}},
    ])
    ROUND2 = AIMessage(content="", tool_calls=[
        {"id": "ct3", "name": "web_search", "args": {"query": "鸡丝凉面 少油少盐 做法"}},
    ])

    class _ScriptedLLM:
        """按顺序回放「并排搜 2 个 → 再搜 1 个 → 自己写完整讲解」。"""

        def __init__(self, turns):
            self.turns = turns
            self.index = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, payload):
            response = self.turns[min(self.index, len(self.turns) - 1)]
            self.index += 1
            return response

    class _FakeExecutor:
        def __init__(self):
            self.executed = []

        def invoke(self, state):
            calls = list(getattr(state["messages"][-1], "tool_calls") or [])
            self.executed.append([c["name"] for c in calls])
            return {"messages": [
                ToolMessage(content="{}", tool_call_id=c["id"], name=c["name"])
                for c in calls
            ]}

    @staticmethod
    def _visible_answer(messages):
        """取用户真正看到的正文：跳过结构化 payload（JSON）和带 tool_calls 的空消息。"""
        for message in reversed(messages or []):
            if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
                text = str(message.content or "")
                if text.strip().startswith("{"):
                    continue
                return text
        return ""

    def _stub_structured_answer(self, context):
        """结构化链离线替身：选定轮的卡片内容不是本用例的断言对象。"""
        return ChefAnswer(recipes=[Recipe(
            name="鸡丝黄瓜凉拌面",
            intro="清爽少油的手撕鸡丝凉面",
            difficulty=2,
            nutrition=4,
            seasonings=[Seasoning(name="生抽", amount="一勺")],
            steps=["鸡胸肉煮熟手撕", "面条过凉水后拌匀"],
        )])

    def _run_pick_turn(self):
        llm = self._ScriptedLLM([self.ROUND1, self.ROUND2, AIMessage(content=self.OPENING)])
        executor = self._FakeExecutor()
        state_in = {
            "messages": add_messages([], [
                HumanMessage(content="我有鸡胸肉、黄瓜和面条"),
                AIMessage(content=json.dumps(
                    _candidates_payload(self.CANDIDATES), ensure_ascii=False,
                )),
                HumanMessage(content=(
                    "【已选定候选：鸡丝黄瓜凉拌面】\n"
                    "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开。）\n\n"
                    "3"
                )),
            ]),
            "session_id": "pick-turn-graph-test",
        }
        app = g.workflow.compile(checkpointer=MemorySaver())
        trace_path = Path(tempfile.gettempdir()) / "probe_pick_turn_graph_test.jsonl"
        final = None
        with patch.object(g, "llm_with_tools", llm), \
                patch.object(g, "llm", llm), \
                patch.object(g, "summary_llm", llm), \
                patch.object(g, "retrieval_llm", llm), \
                patch.object(g, "tool_executor", executor), \
                patch.object(g, "build_structured_answer", self._stub_structured_answer), \
                patch.object(g, "rank_recipes", lambda answer, **kwargs: answer), \
                patch.object(g, "_search_recipe_image", lambda name: (None, False)), \
                patch.object(trace_mod, "_FILE", trace_path):
            for chunk in app.stream(
                state_in,
                {"configurable": {"thread_id": "pick-turn-graph-test"}},
                stream_mode="values",
            ):
                final = chunk
        return final, executor

    def test_second_round_tool_call_is_executed(self):
        final, executor = self._run_pick_turn()
        # 节点能读到 tool_budget / tool_calls_in_turn → 第二圈补到额度 → 真的执行出去
        self.assertEqual(
            executor.executed,
            [["web_search", "nutrition_kb_search"], ["web_search"]],
            "选定轮第二圈的工具调用没被执行 —— 先查 chef_think 的 input_schema 是否被裁成 messages",
        )
        self.assertFalse(
            final.get("tool_budget_exhausted"),
            "选定轮第二圈被判预算耗尽 → 下一跳就是把正文换成兜底句",
        )

    def test_pick_turn_keeps_model_opening(self):
        final, _ = self._run_pick_turn()
        messages = final.get("messages") or []
        self.assertEqual(self._visible_answer(messages).strip(), self.OPENING)
        joined = " ".join(str(getattr(m, "content", "")) for m in messages)
        self.assertNotIn(
            self.FALLBACK_MARK,
            joined,
            "用户已经看到的讲解被 tool_budget_finalize 的兜底句顶掉了",
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
        with patch("agent.graph.build_structured_answer", return_value=_chef_answer(["番茄鸡蛋羹"])), \
                patch("agent.graph._build_structure_context", return_value=("上下文", None, False)), \
                patch("agent.graph._allergens_for_audit", return_value=[]):
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
        """候选模板保留短候选理由，但不把它混同为完整菜谱讲解。"""
        tpl = g.CANDIDATE_LIST_RULE_TEMPLATE.format(count=3)
        for key in ["用途/场合", "约 30 字", "永不退让", "不是「能不能吃」", "人性化", "25 字以内"]:
            with self.subTest(key=key):
                self.assertIn(key, tpl)


class KbSourceBackfillTest(unittest.TestCase):
    """亮点4（可溯源）：模型漏填 sources 时，由代码按本轮 KB 命中确定性回填。

    只回填来源文件与章节——语义是「本轮检索到这些依据」，不声称每条健康结论
    与片段逐一对应；模型已给出 sources 时一字不动，没有命中时保持空列表。
    """

    DISH = "清蒸鲈鱼"
    INFANT_SOURCE = "中国婴幼儿喂养状况白皮书（2022）.pdf"
    GB_SOURCE = "GB 7718-2025 预包装食品标签通则.pdf"

    def _real_hits(self):
        # 真实 nutrition_kb_search 返回形状：只有 excerpt，没有 text
        return [
            {"source": "中国居民膳食指南2022.pdf", "section": "少盐",
             "distance": 0.12, "excerpt": "成人每天食盐不超过5克。",
             "metadata": {"anchor": "p42"}},
            {"source": "成人高血压食养指南（2023年版）", "section": "限盐",
             "distance": 0.2, "excerpt": "高血压患者应减少钠摄入。",
             "metadata": {"anchor": "p10"}},
        ]

    def _kb_message(self, hits, *, found=True):
        return ToolMessage(
            content=json.dumps({"found": found, "hits": hits}, ensure_ascii=False),
            tool_call_id="kb1",
            name="nutrition_kb_search",
        )

    def _messages(self):
        return [
            HumanMessage(content="帮我做道清蒸鲈鱼"),
            AIMessage(content="", tool_calls=[
                {"name": "nutrition_kb_search", "args": {"query": "低盐"}, "id": "kb1"},
            ]),
            self._kb_message(self._real_hits()),
            AIMessage(content=f"{self.DISH}的关键是控制用盐，下面给你完整做法。"),
        ]

    def _audience_hits(self):
        return [
            {"source": self.INFANT_SOURCE, "section": "婴幼儿喂养", "excerpt": "..."},
            {"source": self.GB_SOURCE, "section": "致敏物质标示", "excerpt": "..."},
        ]

    def _state(self):
        return {"messages": self._messages(), "verify_status": "ok", "verify_violated": []}

    def test_kb_source_refs_extracts_source_and_section(self):
        refs = g._kb_source_refs(self._messages())
        self.assertEqual(refs, [
            {"source": "中国居民膳食指南2022.pdf", "section": "少盐"},
            {"source": "成人高血压食养指南（2023年版）", "section": "限盐"},
        ])

    def test_kb_source_refs_dedupes_and_caps(self):
        hits = [{"source": "同一份.pdf", "section": "同一条"}] * 3 + [
            {"source": "另一份.pdf", "section": "条目A"},
            {"source": "第三份.pdf", "section": "条目B"},
            {"source": "第四份.pdf", "section": "条目C"},
        ]
        refs = g._kb_source_refs([HumanMessage(content="来一道"), self._kb_message(hits)])
        self.assertLessEqual(len(refs), 3)
        # 同一 (文件, 章节) 只留一条
        self.assertEqual(refs[0], {"source": "同一份.pdf", "section": "同一条"})
        self.assertEqual(
            [ref["source"] for ref in refs],
            ["同一份.pdf", "另一份.pdf", "第三份.pdf"],
        )

    def test_kb_source_refs_empty_without_hits(self):
        self.assertEqual(g._kb_source_refs(self._messages()[:1]), [])
        self.assertEqual(g._kb_source_refs([self._kb_message([], found=False)]), [])

    def test_structure_node_backfills_sources_when_model_omits(self):
        answer = _chef_answer([self.DISH])  # sources 默认空列表
        with (
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(self._state())
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["recipes"][0]["name"], self.DISH)
        self.assertEqual(payload["sources"], [
            {"source": "中国居民膳食指南2022.pdf", "section": "少盐",
             "snippet": "", "category": ""},
            {"source": "成人高血压食养指南（2023年版）", "section": "限盐",
             "snippet": "", "category": ""},
        ])

    def test_kb_source_refs_filters_infant_source_for_adult_context(self):
        messages = [
            HumanMessage(content="帮我做道清蒸鲈鱼"),
            self._kb_message(self._audience_hits()),
        ]
        refs = g._kb_source_refs(messages)
        self.assertEqual(
            refs,
            [{"source": self.GB_SOURCE, "section": "致敏物质标示"}],
        )

    def test_kb_source_refs_allows_infant_source_for_infant_context(self):
        messages = [
            HumanMessage(content="给2岁宝宝做一道清蒸鲈鱼"),
            self._kb_message(self._audience_hits()),
        ]
        refs = g._kb_source_refs(messages)
        self.assertEqual(
            [ref["source"] for ref in refs],
            [self.INFANT_SOURCE, self.GB_SOURCE],
        )

    def test_structure_node_keeps_matching_model_source(self):
        answer = _chef_answer([self.DISH])
        answer.sources = [SourceRef(source="模型自己填的.pdf", section="章节", snippet="片段")]
        with (
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._active_profile_audience_text", return_value=""),
        ):
            result = g.structure_answer_node(self._state())
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["sources"][0]["source"], "模型自己填的.pdf")
        self.assertEqual(len(payload["sources"]), 1)

    def test_structure_node_filters_model_source_and_backfills_safe_kb_source(self):
        answer = _chef_answer([self.DISH])
        answer.sources = [
            SourceRef(source=self.INFANT_SOURCE, section="婴幼儿喂养", snippet="..."),
        ]
        messages = [
            HumanMessage(content="帮我做道清蒸鲈鱼"),
            self._kb_message(self._audience_hits()),
            AIMessage(content=f"{self.DISH}的关键是控制用盐，下面给你完整做法。"),
        ]
        with (
            patch("agent.graph.build_structured_answer", return_value=answer),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._active_profile_audience_text", return_value=""),
        ):
            result = g.structure_answer_node({
                "messages": messages,
                "verify_status": "ok",
                "verify_violated": [],
            })
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(
            payload["sources"],
            [{"source": self.GB_SOURCE, "section": "致敏物质标示",
              "snippet": "", "category": ""}],
        )

    def test_structure_node_leaves_sources_empty_without_kb_hits(self):
        messages = [
            HumanMessage(content="帮我做道清蒸鲈鱼"),
            AIMessage(content=f"{self.DISH}的关键是控制用盐，下面给你完整做法。"),
        ]
        with (
            patch("agent.graph.build_structured_answer", return_value=_chef_answer([self.DISH])),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
        ):
            result = g.structure_answer_node(
                {"messages": messages, "verify_status": "ok", "verify_violated": []}
            )
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["sources"], [])

    def test_structured_allergen_block_backfills_kb_sources(self):
        """结构化复核命中过敏原而取消卡片时，本轮检索到的依据同样要带上。"""
        with (
            patch("agent.graph.build_structured_answer", return_value=_chef_answer([self.DISH])),
            patch("agent.graph._build_structure_context", return_value=("上下文", None, False)),
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph.audit_allergens", return_value=[{"condition": "过敏原:花生"}]),
        ):
            result = g.structure_answer_node(self._state())
        payload = json.loads(result["messages"][-1].content)
        self.assertEqual(payload["recipes"], [])
        self.assertEqual(payload["sources"], [
            {"source": "中国居民膳食指南2022.pdf", "section": "少盐",
             "snippet": "", "category": ""},
            {"source": "成人高血压食养指南（2023年版）", "section": "限盐",
             "snippet": "", "category": ""},
        ])


if __name__ == "__main__":
    unittest.main()
