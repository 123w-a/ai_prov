# tests/domain/test_allergen_guardrail.py
# 过敏原硬护栏回归：规则优先级、图节点阻断、结构化复核、流式转发。

import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from domain import allergen_rules
import agent.graph as agent_graph
import main
from infrastructure import paths as runtime_paths
from storage import memory_candidates
from agent.schemas import ChefAnswer, Recipe, Seasoning


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


class TestRequestedAllergenAddition(unittest.TestCase):
    """画像里的过敏原被用户明确要求加入时，也要确定性命中（归一与否都适用）。"""

    def test_explicit_requested_addition_is_detected(self):
        text = "对第3道菜品进行改造，加入芒果这个水果，做成一个甜品可以吗"
        self.assertEqual(
            allergen_rules.requested_allergen_additions(text, ["芒果"]),
            ["芒果及其制品"],
        )

    def test_negated_requested_addition_is_not_detected(self):
        for text in (
            "不要加芒果",
            "不加芒果",
            "避免放入芒果",
            "芒果过敏，不能加入",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["芒果"]),
                    [],
                )

    def test_member_profile_addition_is_not_treated_as_adding_food(self):
        for text in (
            "妹妹还没有加入家庭画像，请把她加入：对花生过敏，口味清淡",
            "请把妹妹加入：对花生过敏，口味清淡",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["花生"]),
                    [],
                )

    def test_food_addition_still_triggers(self):
        for text in ("把花生加入这道菜", "加花生"):
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["花生"]),
                    ["花生及其制品"],
                )

    def test_profile_addition_variants_never_block_as_food(self):
        """画像加入是「给人建档」，不是「把过敏原下到菜里」。

        这是线上真实翻车场景：用户在补采轮说
        「妹妹还没有加入家庭画像，请把她加入：对花生过敏」，被误判成
        「要求把花生放进菜」→ verify_answer 直接 blocked，用户看到的是
        一道毫无理由的过敏拦截。以下五种说法都必须放行。
        """
        cases = (
            "妹妹还没有加入家庭画像，请把她加入：对花生过敏，口味清淡",
            "小雨还没有加入家庭画像，请把她加入：对花生过敏",
            "请把妹妹加入：对花生过敏，口味清淡",
            "添加家庭成员妹妹，她对花生过敏",
            "给妹妹建档，花生过敏，口味清淡",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["花生"]),
                    [],
                )

    def test_dish_addition_variants_all_block(self):
        """收紧排除规则后，真正把过敏原下到菜里的说法一个都不能漏。

        上一版 ``_PROFILE_ADDITION_RE`` 用了 ``(?:把|将)?`` 可选量词、
        把 ``我`` 放进人物列表、并用 ``.{0,12}`` 跨词，导致
        「我加入花生」这类主谓句被当成画像语境而漏拦。这里逐条钉死。
        """
        peanut_cases = (
            "给我加点花生",
            "把花生加进去",
            "帮我放点花生碎",
            "这道菜加入花生酱",
            "加入花生",
            "给这位加花生",
            "我添加花生",
            "我加入花生",
            "添加花生到这个家庭的菜单",
        )
        for text in peanut_cases:
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["花生"]),
                    ["花生及其制品"],
                )
        # 归一后的水果类过敏原（芒果）同样要命中，不能被别名差异静默吞掉。
        self.assertEqual(
            allergen_rules.requested_allergen_additions("我要加芒果", ["芒果"]),
            ["芒果及其制品"],
        )

    def test_negated_and_unrelated_phrases_stay_clean(self):
        """护栏不能靠「多拦」换安全感：否定句与无关句必须放行。"""
        cases = (
            "不加花生",
            "对第3道菜品进行改造，把花生换掉",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.requested_allergen_additions(text, ["花生"]),
                    [],
                )

    def test_known_gap_include_verb_is_not_a_dish_verb(self):
        """已知缺口（不是本次回归引入）：``纳入`` 不在菜品动词表内。

        ``纳入`` 在中文里主要用于画像/档案语境（「把妹妹纳入家庭画像」），
        因此**刻意**不加入 ``_ADDITION_REQUEST_RE``——补进去会把
        「把芒果纳入推荐清单」一类说法也误判成下料请求，扩大误报面。
        若将来要覆盖「纳入花生」，请连同误报评估一起评估，而不是只加一个词。
        """
        self.assertEqual(
            allergen_rules.requested_allergen_additions("纳入花生", ["花生"]),
            [],
        )

    def test_explicit_no_allergen_values_are_ignored(self):
        for value in ("无", "没有过敏", "无过敏原"):
            with self.subTest(value=value):
                self.assertEqual(allergen_rules.normalize_allergens(value), [])
                self.assertEqual(
                    allergen_rules.unresolved_allergen_advisories([value]),
                    [],
                )

    def test_pending_allergen_is_not_deduplicated_across_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidates_path = Path(temp_dir) / "memory_candidates.json"
            with patch.object(memory_candidates, "_CANDIDATES_PATH", candidates_path):
                first = memory_candidates.extract_candidates(
                    "我对花生过敏",
                    [{"id": "me", "name": "我", "profile": {}}],
                    session_id="session-a",
                )
                memory_candidates.remember_candidates(first)
                second = memory_candidates.extract_candidates(
                    "我对花生过敏",
                    [{"id": "me", "name": "我", "profile": {}}],
                    session_id="session-b",
                )

        self.assertEqual([item["value"] for item in first], ["花生"])
        self.assertEqual([item["value"] for item in second], ["花生"])


class TestChronicRegression(unittest.TestCase):
    def test_ten_existing_nutrition_rules_still_fire(self):
        cases = [
            (["高血压"], "咸菜炒肉"),
            # 裸提名「酱油红烧肉」不再算违规（只有菜名、没有用量，属于证据不足）；
            # 写成用量后折算食盐当量超上限，仍要拦——这里守住的是这条口径。
            (["高血压"], "酱油2汤匙红烧肉"),
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


class TestChronicAuditScope(unittest.TestCase):
    """实测回归：慢病审计只看「菜谱本体」，讲解/依据/护栏字段不进硬审计。

    根因——整篇审计时，模型解释「为什么这道菜要少吃」的句子（指南引用、
    高钠食物清单、JSON 里的 health_lights/guardrails）本身就写着忌口词，
    被判成真实下料，合规回答被反复打回、前端连着闪「正在形成膳食建议」。
    收窄只针对「讲解型正文」；口语菜谱（没有分节、也没有引用/结论词）仍走
    既有的「剥掉安全说明句」口径，真下料的红线一条都不放。
    """

    def _chronic(self, text):
        audit_text, _ = agent_graph._audit_texts_for_answer(
            text, candidate_names=[], plain_safety_refusal=False, allergens=[]
        )
        return audit_text

    def test_json_payload_audits_recipe_fields_only(self):
        payload = json.dumps(
            {
                "answer_kind": "recipes",
                "opening": "这顿撞了红线：猪肝属动物内脏、啤酒属酒精，都正撞痛风忌口。",
                "health_lights": ["高嘌呤（动物内脏）", "酒精（啤酒）"],
                "guardrails": [
                    {
                        "condition": "孩子·痛风",
                        "rule": "限制动物内脏、浓肉汤与酒精",
                        "status": "risk_flag",
                    }
                ],
                "recipes": [
                    {
                        "name": "清蒸鲈鱼",
                        "serving": "2 人份",
                        "ingredients": ["鲈鱼 1 条", "姜丝 10g"],
                        "steps": ["鱼身划刀，铺姜丝，大火蒸 8 分钟。"],
                    }
                ],
            },
            ensure_ascii=False,
        )
        audit_text = self._chronic(payload)
        self.assertIn("清蒸鲈鱼", audit_text)
        self.assertNotIn("health_lights", audit_text)
        self.assertNotIn("啤酒", audit_text)
        self.assertFalse(agent_graph.audit(audit_text, ["痛风"]))

    def test_json_payload_still_audits_a_real_forbidden_ingredient(self):
        payload = json.dumps(
            {
                "opening": "低嘌呤路线",
                "recipes": [
                    {
                        "name": "丝瓜炒虾皮",
                        "ingredients": ["丝瓜 1 根", "虾皮 1 小把"],
                        "steps": ["丝瓜炒软后下虾皮。"],
                    }
                ],
            },
            ensure_ascii=False,
        )
        self.assertTrue(agent_graph.audit(self._chronic(payload), ["痛风"]))

    def test_advisory_prose_without_recipe_body_is_not_audited(self):
        """实测 perf-1791806412 / user_357fdc3df8：口径题与能否吃的问答里，
        「浓肉汤、老火汤、猪肝、酒」是这轮回答的**主题词**，不是下料。

        旧口径认不出菜谱分节就退回整篇，于是同一段讲解每轮都被判违规、
        重生成到 MAX_VERIFY 才收口——前端看到的就是反复闪「正在形成膳食建议」。
        """
        for text in (
            "**结论：成人高血压每天食盐逐步降到 5g 以下。**\n"
            "（出处：《成人高血压食养指南（2023年版）》，第 6 页）\n"
            "**最容易被忽略的隐形盐（对照着减）**\n"
            "- 蚝油、鸡精、味精、豆瓣酱、豆豉、腐乳、榨菜、咸菜、酱瓜\n"
            "- 挂面、苏打饼干、面包、火腿肠、卤味、话梅——吃着不咸也有钠\n",
            "## 结论：汤别喝，肉可以吃几块\n"
            "针对妈妈的**痛风 + 尿酸高**，长时间熬的排骨浓汤属于「要严格限制」的一类。\n"
            "- 官方做法建议是「避免摄入浓肉汤、老火汤、菜汤」"
            "（《成人慢性肾脏病食养指南（2024年版）》）。\n",
            "《成人高尿酸血症与痛风食养指南（2024年版）》原文写得很直白："
            "「**动物内脏如肝、肾、心等，嘌呤含量**（高）」——肝是嘌呤含量榜首那一档；"
            "大量酒精和高果糖饮料同样要限制。\n"
            "啤酒换成无糖苏打水，猪肝限量到 50g，汤整锅倒掉。\n",
        ):
            with self.subTest(text=text[:24]):
                audit_text = self._chronic(text)
                self.assertEqual(audit_text, "")

    def test_plain_prose_recipe_still_goes_through_audit(self):
        """边界：认不出分节、也没有引用/结论词的正文是口语菜谱，照旧审计。

        实测 2026-10-09 选定轮：正文写「生抽2大勺、盐5g」被判 retry 是**对的**，
        收窄审计范围不能把这条真下料的红线一起放掉。
        """
        text = "番茄牛肉面：面条煮熟，加生抽2大勺、盐5g调味，铺上牛肉即可。"
        audit_text = self._chronic(text)
        self.assertTrue(agent_graph.audit(audit_text, ["高血压"]))
        # 只报菜名/半句结论的短答复同样不适用讲解豁免（保持基线口径）
        self.assertNotEqual(self._chronic("老火汤炖猪肝"), "")

    def test_mixed_heading_prose_keeps_recipe_body_only(self):
        # 模型常把分节标题写成 `**【用料】**（约3–4人份）`、`【做法】`，
        # 认不出来时整篇讲解会被当成菜谱本体卷进硬审计。
        text = (
            "（痛风人群要避免浓肉汤、老火汤与动物内脏，这份按低嘌呤路线调整。）\n"
            "**【用料】**（约3–4人份）\n"
            "排骨 500g、话梅 5 颗、生抽 2 汤匙\n"
            "**【做法】**\n"
            "1. 排骨冷水下锅焯水，倒掉这锅水（飞水弃汤）。\n"
            "2. 加生抽 3g 与话梅焖 40 分钟。\n"
        )
        audit_text = self._chronic(text)
        self.assertIn("排骨 500g", audit_text)
        self.assertNotIn("痛风人群要避免", audit_text)
        self.assertFalse(agent_graph.audit(audit_text, ["痛风"]))
        self.assertTrue(agent_graph.audit(audit_text, ["高血压"]))

    def test_sectioned_prose_keeps_recipe_body_only(self):
        text = (
            "结论：成人高血压每天食盐逐步降到 5g 以下"
            "（《成人高血压食养指南（2023年版）》）。\n"
            "- 隐形钠清单：挂面、火腿肠、咸菜、话梅。\n"
            "食材：番茄 2 个、嫩豆腐 1 盒。\n"
            "做法：番茄炒出汁后下豆腐，出锅前加盐 1g。\n"
        )
        audit_text = self._chronic(text)
        self.assertIn("嫩豆腐", audit_text)
        self.assertIn("盐 1g", audit_text)
        self.assertNotIn("咸菜", audit_text)
        self.assertFalse(agent_graph.audit(audit_text, ["高血压"]))


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

    def test_negated_allergen_mentions_are_not_treated_as_ingredients(self):
        cases = [
            ("本菜绝不加虾皮、虾米、海鲜酱", ["crustacean", "fish"]),
            ("本菜不放虾米", ["crustacean"]),
            ("本菜不含海鲜酱", ["fish"]),
            ("全程避免虾皮", ["crustacean"]),
            ("底料绝无虾皮", ["crustacean"]),
            ("爷爷虾过敏，本菜不安排相关食材", ["crustacean"]),
            (
                "这道菜完全不进虾，也别用煮过虾的水或同一口未洗的锅。",
                ["crustacean"],
            ),
            (
                "虾是爷爷的硬禁区，他那份全程不碰虾，"
                "也不与虾共用同一口锅、同一把铲，避免交叉污染。",
                ["crustacean"],
            ),
            (
                "西兰花豆腐羹（含虾？—— 这一锅绝对不放虾）",
                ["crustacean"],
            ),
            (
                "这道菜含虾吗？不放虾，也不含虾皮。",
                ["crustacean"],
            ),
            (
                "清水或自熬鸡汤 600ml；❗虾、虾皮、虾米一律不加",
                ["crustacean"],
            ),
            (
                "本菜及任何替代料均不得含虾、虾皮、虾米。",
                ["crustacean"],
            ),
            (
                "全部菜品必须不含虾及任何虾制品。",
                ["crustacean"],
            ),
        ]
        for text, allergens in cases:
            with self.subTest(text=text):
                self.assertEqual(allergen_rules.audit_allergens(text, allergens), [])

    def test_positive_allergen_mentions_still_fire_after_negation(self):
        cases = [
            ("虾仁炒蛋", "虾仁"),
            ("菜里加了虾皮", "虾皮"),
            ("推荐虾米蒸蛋", "虾米"),
            ("不加虾皮，改加虾米", "虾米"),
            ("用煮过虾的水做汤", "虾"),
            ("虾是主料", "虾"),
            ("不与虾共用锅，但成品里有虾仁", "虾仁"),
            ("含虾？是的，确实加了虾", "虾"),
            ("含虾吗？确定有虾仁，正常使用", "虾"),
            ("虾、虾皮、虾米一律加入", "虾"),
                    ("虾制品是主料", "虾"),
        ]
        for text, expected_keyword in cases:
            with self.subTest(text=text):
                result = allergen_rules.audit_allergens(text, ["crustacean"])
                self.assertTrue(result)
                self.assertEqual(result[0]["keyword"], expected_keyword)

    def test_exclusive_negation_is_not_treated_as_ingredient(self):
        """「非」也是否定词，安全说明不得当成真下料。

        实测翻车点（2026-10-10，会话 user_c9b101adf6）：分餐矩阵写
        「只需确认用油非花生油、砧板锅具没沾花生碎」，被判定命中「花生油」，
        verify_answer 打回重生成 ⇒ 用户看到同一道菜讲了两遍。
        同句的「没沾花生碎」是同类误报，一并锁住。
        """
        cases = (
            "只需确认用油非花生油、砧板锅具没沾花生碎",
            "砧板与锅具不粘花生粉，也不沾花生碎",
            "本菜用油非花生油。",
            "本菜用油并非花生油。",
            "本菜用油而非花生油。",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    allergen_rules.audit_allergens(text, ["peanut"]), []
                )

    def test_exclusive_negation_does_not_excuse_real_use(self):
        """「非」只放宽安全说明；强调式双重否定与「非法/非转基因」仍必须拦。"""
        cases = (
            ("非转基因花生油", "花生油"),
            ("非法添加花生", "花生"),
            ("非花生油不可", "花生油"),
            ("这道菜非用花生油不可", "花生油"),
        )
        for text, expected_keyword in cases:
            with self.subTest(text=text):
                result = allergen_rules.audit_allergens(text, ["peanut"])
                self.assertTrue(result, f"{text} 属于真下料，必须拦下")
                self.assertEqual(result[0]["keyword"], expected_keyword)


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

    def test_optional_mango_aliases_block_with_exclusion(self):
        for text in ("芒果", "芒果干", "芒果汁", "杨枝甘露"):
            with self.subTest(text=text):
                result = allergen_rules.audit_allergens(
                    text,
                    ["mango"],
                    use_optional=True,
                )
                self.assertTrue(result)
                self.assertEqual(result[0]["condition"], "过敏原:芒果及其制品")
        self.assertEqual(
            allergen_rules.audit_allergens(
                "芒果核",
                ["mango"],
                use_optional=True,
            ),
            [],
        )

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

    def test_compound_food_aliases_expand_to_all_relevant_categories(self):
        self.assertCountEqual(
            allergen_rules.normalize_allergens("酱油"),
            ["gluten", "soy"],
        )
        self.assertCountEqual(
            allergen_rules.normalize_allergens("沙拉酱"),
            ["egg", "milk"],
        )
        self.assertEqual(
            allergen_rules.normalize_allergens("麻酱"),
            ["sesame"],
        )
        self.assertEqual(
            allergen_rules.normalize_allergens("芒果汁"),
            ["mango"],
        )

    def test_upper_term_does_not_hide_other_allergens(self):
        self.assertCountEqual(
            allergen_rules.normalize_allergens("海鲜、花生、乳制品"),
            ["crustacean", "fish", "peanut", "milk"],
        )

    def test_unresolved_is_logged_not_silent(self):
        allergen_rules._UNRESOLVED_ALREADY_LOGGED.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            # 日志已从项目内 data/ 迁到项目外，patch 目标随之改为 LOG_DIR
            log_dir = Path(temp_dir) / "logs"
            with patch.object(runtime_paths, "LOG_DIR", log_dir):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    result = allergen_rules.normalize_allergen("完全未知的忌口")
                log_path = log_dir / "allergen_unresolved.log"
                self.assertIsNone(result)
                self.assertTrue(caught)
                self.assertIn("完全未知的忌口", log_path.read_text(encoding="utf-8"))
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

    def test_safe_suggestions_honor_chronic_conditions(self):
        candidates = [
            ("油焖大虾", "虾 盐"),
            ("清炒青菜", "青菜 盐"),
        ]
        with patch.object(allergen_rules, "_SAFE_DISH_CANDIDATES", candidates):
            dishes = allergen_rules.suggest_safe_dishes(
                [],
                k=3,
                conditions=["痛风"],
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
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.verify_answer_node(self._state("推荐油焖大虾"))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("过敏原是绝对硬约束", result["messages"][0].content)

    def test_pending_session_allergens_join_deterministic_audit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            candidates_path = Path(temp_dir) / "memory_candidates.json"
            with patch.object(memory_candidates, "_CANDIDATES_PATH", candidates_path):
                candidates = memory_candidates.extract_candidates(
                    "我对花生和芝麻过敏",
                    [{"id": "me", "name": "我", "profile": {}}],
                    session_id="pending-allergen-session",
                )
                memory_candidates.remember_candidates(candidates)
                state = self._state(
                    "推荐花生拌面",
                    session_id="pending-allergen-session",
                )
                with patch(
                    "agent.graph._active_profile_allergens",
                    return_value=["虾"],
                ):
                    result = agent_graph.verify_answer_node(state)

        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_single_recipe_safety_notes_do_not_trigger_retry(self):
        text = (
            "西兰花豆腐羹（含虾？—— 这一锅绝对不放虾）；"
            "本菜绝不加虾皮、虾米、海鲜酱（爷爷虾过敏硬约束）；"
            "蚝油、鸡精也一并省掉。"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["虾"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_real_cross_contamination_notes_do_not_trigger_retry(self):
        text = (
            "这道菜完全不进虾，也别用煮过虾的水或同一口未洗的锅。"
            "虾是爷爷的硬禁区，他那份全程不碰虾，"
            "也不与虾共用同一口锅、同一把铲，避免交叉污染。"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["虾"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_selected_recipe_safety_explanation_does_not_trigger_retry(self):
        text = (
            "含花生及其制品的做法我不能推荐，已换成安全替代：蒜蓉蒸丝瓜。\n"
            "食材：丝瓜、大蒜、玉米油、香油。\n"
            "做法：蒸熟后拌匀。孩子那份避免交叉接触。"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_selected_recipe_actual_allergen_still_retries(self):
        text = "蒜蓉蒸丝瓜。食材：丝瓜、花生酱、大蒜。"
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_recipe_safety_reminders_mentioning_allergen_do_not_retry(self):
        """实测翻车：无花生宫保鸡丁的好答案里，「顶替花生」「配料表无花生」
        「少一份油炸花生」都是提醒/替代说明，旧口径把它们当成真下料，连续
        重生成后前端只剩一句兜底。硬审计只认食材/做法本体，提醒段不再重打回。
        """
        text = (
            "## 健康护栏\n"
            "- 孩子确诊花生过敏 → 整锅绝对不能出现花生。\n"
            "- 脆感替代：用黄瓜丁 + 胡萝卜丁顶替花生，口感一样脆。\n"
            "## 今日主菜：宫保鸡丁（无花生·低盐家庭版）\n"
            "**食材（2-3人份）**\n"
            "| 主料 | 鸡胸肉 250g |\n"
            "| 脆口替代 | 黄瓜半根、胡萝卜半根 |\n"
            "**做法**\n"
            "1. 鸡丁腌十分钟，滑炒至变色。\n"
            "2. 出锅前点几滴不含花生的辣椒油。\n"
            "## 热量-运动当量\n"
            "无花生版比传统版少一份油炸花生，热量约低 90 千卡。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_recipe_ingredient_table_allergen_still_retries_after_core_scope(self):
        """收窄硬审计范围后，食材表里的真下料仍要拦：替代说明与真下料必须区分开。"""
        text = (
            "## 说明\n"
            "- 脆感替代：用黄瓜丁顶替花生。\n"
            "**食材**\n"
            "| 主料 | 鸡胸肉 |\n"
            "| 调味 | 生抽、糖、花生碎 1 小把 |\n"
            "**做法**\n"
            "1. 合炒出锅。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_negated_allergen_notes_inside_recipe_core_do_not_retry(self):
        """实测 E1/E2：无花生宫保鸡丁的好答案，在**食材分节里**写「（替花生）」
        「替代花生米的脆感」「不用花生油」。它们字面含过敏原名却是「不加」，
        旧口径把它们当真实下料，连打好回后前端只剩一道替换菜。
        """
        text = (
            "## 主菜：无花生版宫保鸡丁\n"
            "**食材（2-3人份）**\n"
            "- 鸡胸肉 300g，切 1.5cm 丁\n"
            "- 黄瓜半根、胡萝卜半根 → 切丁，替代花生米的脆感\n"
            "- 藕丁 80g（替花生）、花椒 1 小勺\n"
            "- 玉米油 2 勺 —— 不用花生油\n"
            "**做法**\n"
            "1. 鸡丁腌 10 分钟后滑炒，合炒出锅。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_negated_note_does_not_excuse_real_ingredient_in_same_section(self):
        """豁免只针对「紧邻否定/替代标记」的那一次提及，同分节里的真下料照旧拦。"""
        text = (
            "**食材（2-3人份）**\n"
            "- 藕丁 80g（替花生）\n"
            "- 生抽、糖、花生碎 1 小把\n"
            "**做法**\n"
            "1. 合炒出锅。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_enumerated_negation_line_across_compounds_does_not_retry(self):
        """实测 E2：同一行「不用花生、花生碎、花生油、花生酱」是列举式否定。

        旧口径逐词删除：先吃掉「不用花生」、再吃掉「碎」前面的「花生」，剩下的
        「花生油/花生酱」反而丢了「不用」语境，被当真下料 —— 好答案被打回、最后
        落成一句兜底。整行判定后，这一行整体不进审计，答案应放行。
        """
        text = (
            "**食材（2-3人份）**\n"
            "- 鸡胸肉 300g、黄瓜半根、胡萝卜半根\n"
            "- 玉米油 2 勺\n"
            "**做法**\n"
            "1. 滑炒鸡丁后合炒出锅。\n"
            "关键安全点：\n"
            "- 不用花生、花生碎、花生油、花生酱。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")
        self.assertEqual(result["verify_attempts"], 1)
        self.assertNotIn("messages", result)

    def test_real_allergen_ingredient_after_negation_line_still_retries(self):
        """整行否定只豁免那一行；同一分节里另起一行的真下料照样拦。"""
        text = (
            "**食材（2-3人份）**\n"
            "- 不用花生、花生碎、花生油、花生酱。\n"
            "- 生抽、糖、花生碎 1 小把\n"
            "**做法**\n"
            "1. 合炒出锅。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_allergen_exhaustion_blocks_instead_of_degrading(self):
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.verify_answer_node(
                self._state("推荐油焖大虾", attempts=agent_graph.MAX_VERIFY)
            )
        self.assertEqual(result["verify_status"], "blocked")
        self.assertFalse(result.get("verify_warning"))

    def test_tool_budget_does_not_degrade_allergen(self):
        with patch("agent.graph._allergens_for_audit", return_value=["花生"]):
            result = agent_graph.verify_answer_node(
                self._state("花生拌面", tool_budget_exhausted=True)
            )
        self.assertEqual(result["verify_status"], "blocked")

    def test_chronic_exhaustion_keeps_degraded(self):
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风"]),
        ):
            result = agent_graph.verify_answer_node(
                self._state("老火汤炖猪肝", attempts=agent_graph.MAX_VERIFY)
            )
        self.assertEqual(result["verify_status"], "degraded")
        self.assertTrue(result["verify_warning"].startswith("⚠️"))

    def test_chronic_audit_skips_advisory_sections(self):
        """实测 user_c5d8dcc6b7：提醒段写「别用浓肉汤、别加小鱼小虾类干货」是
        安全说明，不是在下料；按整篇审计时它会让一份合规菜谱反复重生成，
        前端连着闪好几次「正在形成膳食建议」。
        """
        text = (
            "## 番茄豆腐蛋花汤\n"
            "**食材（2人份）**\n"
            "- 番茄 2 个、嫩豆腐 1 盒、鸡蛋 2 个\n"
            "- 淡盐水 1 碗\n"
            "**做法**\n"
            "1. 番茄切块炒出汁，加水烧开后下豆腐。\n"
            "2. 关火后加盐 1g，淋蛋液。\n"
            "**提醒**\n"
            "- 别用浓肉汤、鱼汤打底，也别加小鱼小虾类干货。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风", "高血压"]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")

    def test_chronic_audit_still_retries_real_forbidden_ingredient(self):
        """收窄审计范围后，食材表里的真忌口仍要拦：虾皮照旧命中痛风。"""
        text = (
            "**食材（2人份）**\n"
            "- 番茄 2 个、虾皮 1 小把\n"
            "**做法**\n"
            "1. 番茄炒出汁后下虾皮。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风"]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("虾", result["messages"][0].content)

    def test_chronic_audit_does_not_retry_advisory_answer(self):
        """实测 perf-1791807060：用户问「成人高血压每天盐应该吃多少」，回答里的
        隐形盐清单（火腿肠、咸菜）与指南引用必须出现；按整篇审计会一直判违规，
        连续重生成，前端反复闪「正在形成膳食建议」。
        """
        text = (
            "**成人高血压：每天食盐逐步降至 5g 以下**"
            "（《成人高血压食养指南（2023年版）》）\n"
            "- 隐形钠：挂面、面包、火腿肠、咸菜、腐乳、话梅、苏打饼干。\n"
            "- 一啤酒瓶盖平装约 6g 盐，高血压建议不到一瓶盖。\n"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["高血压"]),
        ):
            result = agent_graph.verify_answer_node(self._state(text))
        self.assertEqual(result["verify_status"], "ok")

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
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
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
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
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

    def test_blocked_payload_backfills_kb_sources(self):
        """阻断轮也要标依据：本轮检索过权威条目就带上（只给文件+章节）。"""
        kb = ToolMessage(
            content=json.dumps({"found": True, "hits": [
                {"source": "成人高血压食养指南（2023年版）", "section": "限盐"},
                {"source": "中国居民膳食指南2022.pdf", "section": "少盐"},
            ]}, ensure_ascii=False),
            tool_call_id="kb1",
            name="nutrition_kb_search",
        )
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.allergen_block_node({
                "messages": [HumanMessage(content="今晚吃什么"), kb],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        payload = json.loads(result["messages"][0].content)
        self.assertEqual(payload["recipes"], [])
        self.assertEqual(payload["sources"], [
            {"source": "成人高血压食养指南（2023年版）", "section": "限盐",
             "snippet": "", "category": ""},
            {"source": "中国居民膳食指南2022.pdf", "section": "少盐",
             "snippet": "", "category": ""},
        ])

    def test_blocked_payload_without_kb_keeps_sources_empty(self):
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
            result = agent_graph.allergen_block_node({
                "messages": [HumanMessage(content="今晚吃什么")],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        payload = json.loads(result["messages"][0].content)
        self.assertEqual(payload["sources"], [])

    def test_blocked_status_never_rendered_as_adjusted(self):
        """分支顺序回归：blocked 必须先判，否则会落进 adjusted 分支变成不实表述。"""
        with patch("agent.graph._allergens_for_audit", return_value=[]):
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
            patch("agent.graph._allergens_for_audit", return_value=["芝麻过敏"]),
            patch(
                "agent.graph.suggest_safe_dishes",
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
            patch("agent.graph._allergens_for_audit", return_value=["虾"]),
            patch("agent.graph.suggest_safe_dishes", return_value=["清炒青菜"]),
        ):
            result = agent_graph.allergen_block_node({
                "messages": [],
                "verify_violated": ["过敏原:甲壳纲类动物及其制品"],
            })
        payload = json.loads(result["messages"][0].content)
        self.assertIn("其他无该过敏原的成员仍可按原菜就餐", payload["opening"])
        self.assertNotIn("全家过敏原并集", payload["opening"])


class TestStructuredReaudit(unittest.TestCase):
    def test_requested_dish_gate_blocks_chronic_before_generation(self):
        state = {
            "messages": [HumanMessage(content="做一道油焖大虾，家里有人痛风")],
            "session_id": "requested-dish-chronic-gate",
            "verify_violated": [],
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风"]),
            patch("agent.graph.suggest_safe_dishes", return_value=["清炒青菜"]),
        ):
            result = agent_graph.requested_dish_gate_node(state)
        payload = json.loads(result["messages"][0].content)
        self.assertEqual(result["verify_status"], "blocked")
        self.assertEqual(result["verify_violated"], ["痛风"])
        self.assertEqual(payload["recipes"], [])
        self.assertIsNone(payload["image_url"])
        self.assertIn("有该约束的成员需单独替换", payload["opening"])
        self.assertNotIn("全家", payload["opening"])
        blocked = [item for item in payload["guardrails"] if item["status"] == "blocked"]
        self.assertEqual([item["condition"] for item in blocked], ["痛风"])
        self.assertIn("命中该约束", blocked[0]["reason"])
        self.assertNotIn("过敏原", blocked[0]["reason"])

    def test_requested_dish_chronic_fallback_skips_structure_model(self):
        state = {
            "messages": [
                HumanMessage(content="做一道油焖大虾，家里有人痛风"),
                AIMessage(content="我来整理这道菜。"),
            ],
            "session_id": "requested-dish-chronic-structure",
            "verify_violated": [],
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风"]),
            patch("agent.graph._build_structure_context", return_value=("可结构化上下文", None, False)),
            patch("agent.graph.build_structured_answer") as build,
        ):
            result = agent_graph.structure_answer_node(state)
        payload = json.loads(result["messages"][0].content)
        build.assert_not_called()
        self.assertEqual(result["verify_status"], "blocked")
        self.assertEqual(payload["recipes"], [])

    def test_pure_safety_question_does_not_hit_requested_dish_gate(self):
        state = {
            "messages": [HumanMessage(content="家里有人痛风，能吃油焖大虾吗")],
            "verify_violated": [],
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=[]),
            patch("agent.graph._merged_conditions", return_value=["痛风"]),
        ):
            self.assertFalse(agent_graph._requested_dish_gate_hit(state))

    def test_requested_dish_with_confirmed_allergen_blocks_before_structure(self):
        answer = ChefAnswer(
            recipes=[
                Recipe(
                    name="芝麻酱拌面",
                    intro="无花生替代",
                    difficulty=1,
                    nutrition=3,
                    seasonings=[Seasoning(name="纯芝麻酱", amount="10g")],
                    steps=["煮面后拌匀即可。"],
                )
            ],
            chef_tip="",
        )
        state = {
            "messages": [
                HumanMessage(content="请给我推荐一道花生酱拌面"),
                AIMessage(content="已重新生成安全替代"),
            ],
            "session_id": "allergen-opening-test",
            "verify_violated": [],
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._build_structure_context", return_value=("可结构化上下文", None, False)),
            patch("agent.graph.build_structured_answer", return_value=answer) as build,
        ):
            result = agent_graph.structure_answer_node(state)
        payload = json.loads(result["messages"][0].content)
        build.assert_not_called()
        self.assertEqual(result["verify_status"], "blocked")
        self.assertEqual(payload["recipes"], [])
        self.assertIn("不能推荐", payload["opening"])
        self.assertIn("花生酱", payload["opening"])
        self.assertIn("替代方向", payload["opening"])
        self.assertNotIn("芝麻酱拌面", payload["opening"])

    def test_negated_requested_allergen_is_not_misblocked(self):
        answer = ChefAnswer(recipes=[Recipe(
            name="清汤面",
            intro="不含花生",
            difficulty=1,
            nutrition=3,
            seasonings=[Seasoning(name="盐", amount="1g")],
            steps=["煮面。"],
        )], chef_tip="")
        state = {
            "messages": [
                HumanMessage(content="推荐一道不含花生的面"),
                AIMessage(content="清汤面不含花生。"),
            ],
            "session_id": "allergen-negative-request-test",
            "verify_violated": [],
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._build_structure_context", return_value=("可结构化上下文", None, False)),
            patch("agent.graph.build_structured_answer", return_value=answer) as build,
        ):
            result = agent_graph.structure_answer_node(state)
        build.assert_called_once()
        self.assertNotEqual(result.get("verify_status"), "blocked")

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
            # 2026-10-20：原输入「推荐一道面」是泛推荐轮。b6e105b 之后候选轮抠不出
            # 候选名时直接返回纯文本、不再落 build_structured_answer，本用例因此
            # 走不到结构化复核（KeyError: verify_status），测不到目标路径。
            # 改用真实选定轮（候选清单 + 回复序号），让卡片确实被生成、再被复核拦下。
            "messages": [
                HumanMessage(content="推荐一道面"),
                AIMessage(content="1. 清汤面\n2. 花生拌面\n3. 牛肉面"),
                HumanMessage(content="第2个"),
            ]
        }
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._build_structure_context", return_value=("可结构化上下文", None, False)),
            patch("agent.graph.build_structured_answer", return_value=answer),
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
        with patch("agent.graph._allergens_for_audit", return_value=["虾"]):
            items = agent_graph._build_guardrails(
                "今晚吃什么",
                "ok",
                ["过敏原:甲壳纲类动物及其制品"],
            )
        allergen_items = [item for item in items if item.condition.startswith("过敏原:")]
        self.assertEqual(len(allergen_items), 1)
        self.assertEqual(allergen_items[0].status, "adjusted")
        self.assertIn("不含", allergen_items[0].rule)

    def test_build_guardrails_includes_pending_session_allergen(self):
        with (
            patch("agent.graph._allergens_for_audit", return_value=["芒果"]),
            patch(
                "storage.memory_candidates.get_session_allergens",
                return_value=["花生"],
            ),
        ):
            items = agent_graph._build_guardrails(
                "推荐一道不含花生的菜",
                "ok",
                [],
                session_id="session-with-pending-allergen",
            )
        conditions = {item.condition for item in items}
        self.assertIn("过敏原:芒果及其制品", conditions)
        # 会话待确认过敏原并入时同样要过 allergen_label() 归一化，
        # 显示名取自规则表 label（花生 -> 花生及其制品），与同文件其他用例一致。
        self.assertIn("过敏原:花生及其制品", conditions)

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

    def test_blocked_structured_payload_is_forwarded_as_answer(self):
        payload = {
            "opening": "不能推荐含虾的菜。",
            "recipes": [],
            "guardrails": [{"condition": "过敏原:虾"}],
        }

        def fake_stream(*args, **kwargs):
            yield (
                "updates",
                {
                    "allergen_block": {
                        "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))]
                    }
                },
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(
                HumanMessage(content="今晚吃虾"),
                "allergen-structured-stream-test",
            ))

        self.assertIn(("answer", payload), events)
        self.assertNotIn(("token", json.dumps(payload, ensure_ascii=False)), events)

    def test_prompt_mentions_deterministic_audit(self):
        rendered = main._render_health_profile({"allergens": ["虾"]})
        self.assertIn("硬约束", rendered)
        self.assertIn("确定性审计", rendered)


class TestInformationTurnRouting(unittest.TestCase):
    """实测 A/D 两条前端可见问题的路由层复现：约束交代不能当安全问答短路。"""

    _FAMILY_REQUEST = (
        "今晚一家三口吃什么？爸爸高血压，妈妈痛风，孩子花生过敏。"
    )
    _SAFETY_QUESTION = "孩子花生过敏，这道菜里能不能放花生酱？"

    def test_family_constraint_request_is_not_information_only(self):
        self.assertFalse(
            agent_graph.is_non_recipe_information_request(self._FAMILY_REQUEST)
        )
        messages = [HumanMessage(content=self._FAMILY_REQUEST)]
        self.assertTrue(agent_graph._is_candidate_turn(messages))

    def test_allergen_permission_question_is_information_request(self):
        self.assertTrue(
            agent_graph.is_non_recipe_information_request(self._SAFETY_QUESTION)
        )
        messages = [HumanMessage(content=self._SAFETY_QUESTION)]
        self.assertFalse(agent_graph._is_candidate_turn(messages))

    def test_plain_safety_answer_is_recognized_as_refusal_turn(self):
        answer = (
            "花生酱属于花生及其制品，孩子花生过敏，这道菜里不能放花生酱。\n"
            "替代方案：换成芝麻酱或直接不放酱，出锅后再单独调味。"
        )
        messages = [HumanMessage(content=self._SAFETY_QUESTION)]
        self.assertTrue(
            agent_graph._is_plain_safety_refusal_turn(
                messages, answer, is_candidate_turn=False
            )
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(
                {
                    "messages": messages + [AIMessage(content=answer)],
                    "verify_attempts": 0,
                }
            )
        self.assertEqual(result["verify_status"], "ok")
        self.assertNotIn("messages", result)

    def test_safety_answer_permitting_allergen_still_retries(self):
        answer = "花生酱很香，可以放一点花生酱提香，孩子也会喜欢。"
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(
                {
                    "messages": [
                        HumanMessage(content=self._SAFETY_QUESTION),
                        AIMessage(content=answer),
                    ],
                    "verify_attempts": 0,
                }
            )
        self.assertEqual(result["verify_status"], "retry")
        self.assertIn("花生", result["messages"][0].content)

    def test_recipe_with_allergen_ingredient_still_retries(self):
        answer = "蒜蓉蒸丝瓜。食材：丝瓜、花生酱、大蒜。"
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(
                {
                    "messages": [
                        HumanMessage(content="做个蒜蓉蒸丝瓜"),
                        AIMessage(content=answer),
                    ],
                    "verify_attempts": 0,
                }
            )
        self.assertEqual(result["verify_status"], "retry")

    def test_plain_safety_answer_mentioning_allergen_in_warnings_is_ok(self):
        answer = (
            "结论：不能放，一口都不要。花生是孩子的绝对过敏原。\n"
            "刚拌过花生酱的勺子、同一块砧板都要单独洗，避免交叉污染。\n"
            "买酱料时看到配料表里加粗的「花生」字样就直接放回货架。\n"
            "替代方案：换成芝麻酱，或者用蒜末生抽香醋拌。"
        )
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(
                {
                    "messages": [
                        HumanMessage(content="孩子花生过敏，这道菜里能不能放花生酱？"),
                        AIMessage(content=answer),
                    ],
                    "verify_attempts": 0,
                }
            )
        self.assertEqual(result["verify_status"], "ok")
        self.assertNotIn("messages", result)

    def test_safety_retry_instruction_stays_on_topic(self):
        answer = "可以放一点花生酱提香，孩子也会喜欢。"
        with (
            patch("agent.graph._allergens_for_audit", return_value=["花生"]),
            patch("agent.graph._merged_conditions", return_value=[]),
        ):
            result = agent_graph.verify_answer_node(
                {
                    "messages": [
                        HumanMessage(content="孩子花生过敏，这道菜里能不能放花生酱？"),
                        AIMessage(content=answer),
                    ],
                    "verify_attempts": 0,
                }
            )
        self.assertEqual(result["verify_status"], "retry")
        instruction = result["messages"][0].content
        self.assertIn("安全疑问", instruction)
        self.assertNotIn("只输出一道菜", instruction)


if __name__ == "__main__":
    unittest.main(verbosity=2)
