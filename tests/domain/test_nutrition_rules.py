# tests/domain/test_nutrition_rules.py
# 硬护栏规则引擎单测：验证 L3 确定性审计（不依赖 LLM）
import unittest
from domain.nutrition_rules import detect_conditions, audit, describe, RULES, SODIUM_SENSITIVE


class TestDetectConditions(unittest.TestCase):
    """用户原话 -> 应启用哪些病种护栏"""

    def test_gout(self):
        self.assertEqual(detect_conditions("我有痛风，尿酸也高"), ["痛风"])

    def test_hypertension(self):
        self.assertEqual(detect_conditions("我血压高"), ["高血压"])

    def test_pregnancy(self):
        self.assertEqual(detect_conditions("我是孕妇"), ["孕期"])

    def test_multi(self):
        # 一句话带多个病种关键词，应全部识别
        found = detect_conditions("痛风又有高血压，怎么吃")
        self.assertIn("痛风", found)
        self.assertIn("高血压", found)

    def test_explicit_negation_is_not_a_condition(self):
        self.assertEqual(detect_conditions("我没有糖尿病，也不是高血压"), [])

    def test_inserted_health_negation_is_not_a_condition(self):
        self.assertEqual(detect_conditions("我没有确诊糖尿病，也并非患有高血压"), [])

    def test_extended_inserted_health_negation(self):
        self.assertEqual(
            detect_conditions("我没有被确诊为糖尿病，也未被诊断患有高血压"),
            [],
        )

    def test_negation_does_not_hide_later_positive_condition(self):
        self.assertEqual(detect_conditions("我没有糖尿病，但我有痛风"), ["痛风"])

    def test_empty(self):
        self.assertEqual(detect_conditions("随便推荐个好吃的"), [])


class TestAuditGoodMenu(unittest.TestCase):
    """合规菜单应零命中"""

    def test_gout_clean(self):
        # 清蒸冬瓜：不含任何痛风 forbidden 词
        vs = audit("推荐清蒸冬瓜，加少许姜丝，清淡少油", ["痛风"])
        self.assertEqual(vs, [], "合规菜单不应命中任何禁忌")

    def test_diabetes_clean(self):
        vs = audit("推荐凉拌黄瓜，用一点点生抽，无糖", ["糖尿病"])
        self.assertEqual(vs, [])

    def test_compliant_salt_wording_is_not_forbidden(self):
        vs = audit("推荐清蒸鱼，全程不加盐，少盐烹饪", ["高血压"])
        self.assertEqual(vs, [])

    def test_reversed_salt_qualifiers_remain_risky(self):
        for text in (
            "这道菜不少盐",
            "不做低盐版本",
            "不能不加盐",
            "并非不加盐",
            "不是少盐版本",
        ):
            vs = audit(text, ["高血压"])
            self.assertTrue(any(v["keyword"] == "盐" for v in vs), text)

    def test_unrelated_grams_are_not_counted_as_salt(self):
        vs = audit("清蒸鱼不加盐，配鸡蛋10克", ["高血压"])
        self.assertFalse(any("食盐" in v["keyword"] for v in vs))

    def test_negated_salt_does_not_hide_real_excess(self):
        vs = audit("本来想不加盐，但实际放盐10克", ["高血压"])
        self.assertTrue(any("食盐" in v["keyword"] for v in vs))

    def test_no_condition_means_no_check(self):
        # 没识别到病种就不审计
        vs = audit("老火汤炖猪肝配啤酒", [])
        self.assertEqual(vs, [])

    def test_trailing_negation_is_not_flagged(self):
        # 2026-10-08 对照实验（experiments/ab_context_qualifier.py）发现的真漏判：
        # 否定落在关键词之后（「酒都不喝」），只查前缀限定词会把安全文本误报成违禁，
        # 重生成轮里模型写合规否定句时会白白再拦一轮。
        vs = audit("拒绝饮酒，任何酒都不喝", ["高血压"])
        self.assertEqual(vs, [])

    def test_trailing_negation_window_does_not_hide_real_hits(self):
        # 后置否定窗口是 4 字符且只认否定词表，真实违禁不受影响（阳性保护）
        vs = audit("晚餐配一瓶冰啤酒", ["痛风"])
        self.assertTrue(vs)
        vs2 = audit("他喝酒", ["痛风"])
        self.assertTrue(vs2)

    def test_verb_bridged_negation_is_not_flagged(self):
        # 对照实验（ab_context_qualifier 2026-10-08）：限定词与关键词隔一个动词
        # （「拒绝**饮**酒」）时否定语义不被单字打断，安全文本不误报
        vs = audit("拒绝饮酒，任何酒都不喝", ["高血压"])
        self.assertEqual(vs, [])
        vs2 = audit("避免喝酒", ["痛风"])
        self.assertEqual(vs2, [])

    def test_verb_bridge_does_not_exempt_reversed_sentences(self):
        # 「拒绝不了喝」是反向句（实际会喝），桥接只认紧贴的动词不认「不了」续接
        vs = audit("他拒绝不了喝酒", ["痛风"])
        self.assertTrue(vs)

    def test_none_text_is_treated_as_empty(self):
        # LLM 空回复（content=None）不得打崩审计；空文本按无违禁
        self.assertEqual(audit(None, ["痛风"]), [])


class TestAuditBadMenu(unittest.TestCase):
    """违禁菜单应被精准拦下"""

    def test_gout_hits_multiple(self):
        bad = "推荐一道老火汤炖猪肝，配啤酒，饭后吃点果糖点心"
        vs = audit(bad, ["痛风"])
        kws = [v["keyword"] for v in vs]
        self.assertIn("老火汤", kws)
        self.assertIn("猪肝", kws)        # 动物内脏
        self.assertIn("啤酒", kws)
        self.assertIn("果糖", kws)
        # 不应重复计（同一 (cond,kw) 已去重）
        self.assertEqual(len(kws), len(set(kws)))

    def test_returns_source_for_traceability(self):
        vs = audit("老火汤炖猪肝", ["痛风"])
        self.assertTrue(all("source" in v and v["source"] for v in vs),
                        "每条命中必须带出处，支撑可溯源演示")

    def test_pregnancy_source_supplemented(self):
        # 孕期规则权威源已补充（4_特殊人群膳食指南 下两份2022指南解读课件），不再标记待补
        vs = audit("吃点生鱼片配酒", ["孕期"])
        self.assertTrue(vs, "孕期违禁应被命中")
        self.assertFalse(any(v.get("todo_source") for v in vs),
                         "孕期规则已补权威源，命中不应再标记 todo_source")
        self.assertTrue(all("待补充" not in v["source"] for v in vs),
                        "孕期命中出处应指向已补充的官方文件，而非'待补充'")


class TestSodiumCap(unittest.TestCase):
    """钠敏感病种应触发食盐上限检查"""

    def test_over_salt_flagged(self):
        # 盐 10g > 上限 5g
        vs = audit("红烧肉，放盐10克，酱油少许", ["高血压"])
        self.assertTrue(any("食盐" in v["keyword"] for v in vs),
                        "超量食盐应触发高钠违规")

    def test_within_salt_ok(self):
        # 盐 5g 等于上限，不算超
        vs = audit("清蒸鱼，放盐5克", ["高血压"])
        self.assertFalse(any("食盐" in v["keyword"] for v in vs),
                         "等于上限不应误报")


class SodiumBillTest(unittest.TestCase):
    """数值账单（2026-10-08 E4 复盘）：超钠反馈必须给逐项明细与差额，
    否则重生成时模型只替换品类、总钠压不下来。"""

    def test_bill_lists_each_source_with_amounts(self):
        vs = audit("红烧肉，放盐2克、生抽3克、鸡精2克", ["高血压"])
        bills = [v for v in vs if v.get("detail")]
        self.assertTrue(bills, "超钠违禁应带 detail 明细")
        items = {d["item"] for d in bills[0]["detail"]}
        self.assertEqual(items, {"盐", "生抽", "鸡精"})
        text = describe(vs)
        self.assertIn("数值账单", text)
        self.assertIn("鸡精2g", text)
        self.assertIn("超", text)
        self.assertIn("仅替换品类", text)

    def test_sour_condiments_now_count_in_sodium(self):
        # E4 实测：模型用味精/鸡精顶替食盐；旧 _salt_total_g 硬编码窄词表算不到
        # 它们（7g 钠里只算得出一小部分）→ 复用 _SALT_SEASONING 后必须进账单
        vs = audit("配菜放鸡精4克、味精3克", ["高血压"])
        bills = [v for v in vs if v.get("detail")]
        self.assertTrue(bills, "鸡精/味精的克数应计入钠账并产生数值违禁")
        items = {d["item"] for d in bills[0]["detail"]}
        self.assertEqual(items, {"鸡精", "味精"})

    def test_honey_counts_in_sugar_bill(self):
        vs = audit("甜品放蜂蜜30g", ["肥胖"])
        sugar = [v for v in vs if v.get("detail")]
        self.assertTrue(sugar, "蜂蜜应计入添加糖账单")
        self.assertIn("蜂蜜", {d["item"] for d in sugar[0]["detail"]})

    def test_under_cap_has_no_bill(self):
        # 注意产品语义：「盐」字词级命中（salt 模式语境判定）与克数超限是两条
        # 独立路径——未超上限时允许词级提示存在，但绝不产生数值账单。
        vs = audit("清蒸鱼，放盐3克", ["高血压"])
        self.assertFalse(any(v.get("detail") for v in vs),
                         "未超上限不应有数值账单")

    def test_total_g_and_detail_stay_consistent(self):
        # 单一数据源绊线：求和必须等于明细之和（长词优先去重语义共用）
        from domain.nutrition_rules import _total_g, _total_g_detail
        text = "白砂糖30g、冰糖10g、蜂蜜5g"
        total = _total_g(text, ["白砂糖", "冰糖", "蜂蜜", "糖"], r"g|克")
        detail = _total_g_detail(text, ["白砂糖", "冰糖", "蜂蜜", "糖"], r"g|克")
        self.assertEqual(total, sum(d["grams"] for d in detail))
        self.assertEqual(total, 45.0)


class TestDescribe(unittest.TestCase):
    """格式化输出"""

    def test_empty(self):
        self.assertEqual(describe([]), "无硬禁忌命中")

    def test_lines(self):
        vs = audit("老火汤炖猪肝", ["痛风"])
        text = describe(vs)
        self.assertIn("痛风", text)
        self.assertIn("来源", text)




class SugarCapTest(unittest.TestCase):
    """肥胖添加糖≤25g/日：克数护栏 audit 消费 sugar_cap_g"""

    def test_obesity_sugar_over_cap_triggers_violation(self):
        violations = audit("甜品放白砂糖30g", ["肥胖"])

        sugar = [v for v in violations if v["keyword"].startswith("糖约")]
        self.assertEqual(len(sugar), 1)
        self.assertIn("30g", sugar[0]["keyword"])
        self.assertIn("25g", sugar[0]["keyword"])

    def test_long_and_short_keyword_do_not_double_count(self):
        violations = audit("甜品放白砂糖30g", ["肥胖"])

        sugar = [v for v in violations if v["keyword"].startswith("糖约")]
        self.assertIn("30g", sugar[0]["keyword"])
        self.assertNotIn("60g", sugar[0]["keyword"])

    def test_sugar_under_cap_stays_silent(self):
        violations = audit("甜汤放冰糖15g", ["肥胖"])

        self.assertFalse(any(v["keyword"].startswith("糖约") for v in violations))

    def test_separate_items_are_summed(self):
        violations = audit("放白砂糖20g、冰糖10g", ["肥胖"])

        sugar = [v for v in violations if v["keyword"].startswith("糖约")]
        self.assertIn("30g", sugar[0]["keyword"])


class TestObesityAlcoholRules(unittest.TestCase):
    """肥胖规则拦截饮酒，但不误伤作为烹饪配料的料酒。"""

    def test_cooking_wine_is_not_flagged(self):
        violations = audit("白灼虾加料酒1平汤勺", ["肥胖"])
        self.assertFalse(any(v["keyword"] == "酒" for v in violations))

    def test_explicit_alcohol_is_still_flagged(self):
        for text in ("配啤酒", "喝白酒", "饮酒", "酒精饮料"):
            with self.subTest(text=text):
                violations = audit(text, ["肥胖"])
                self.assertTrue(violations, text)


class ContextSeriesAndDisclosureTest(unittest.TestCase):
    """E4 第二轮复测暴露的形态（2026-10-08）：并列组限定、数值让路。"""

    def test_conjunction_series_is_qualified(self):
        # 限定词修饰整个并列组：避免[生冷和生食] / 不喝[浓茶和咖啡] / 不放[味精和鸡精]
        self.assertEqual(audit("避免生冷和生食，孕妇远离", ["孕期"]), [])
        self.assertEqual(audit("不喝浓茶和咖啡", ["孕期"]), [])
        self.assertEqual(audit("不放味精和鸡精", ["高血压"]), [])

    def test_series_bridge_without_qualifier_still_flags(self):
        # 桥只豁免「有限定词的组」：无限定词的并列组照拦
        violations = audit("孕期喝浓茶和咖啡", ["孕期"])
        kws = {v["keyword"] for v in violations}
        self.assertTrue({"浓茶", "咖啡"} <= kws, kws)

    def test_series_words_must_come_from_rule_table(self):
        # 组内词必须来自本规则词表：「不放盐和枸杞」里枸杞不在调料表，
        # 盐豁免、枸杞仍拦（若有表内词命中）——用表内/表外各验一次
        self.assertEqual(audit("不放盐和酱油", ["高血压"]), [])
        violations = audit("喝咖啡和奶茶", ["孕期"])
        self.assertEqual({v["keyword"] for v in violations}, {"咖啡"})  # 奶茶不在表

    def test_salt_with_number_moves_to_numeric_layer(self):
        # 用量带数字 → 词级让路；数值超限时由数值层账单接管
        self.assertEqual(audit("少盐烹饪，只放盐3克", ["高血压"]), [])
        violations = audit("放盐8克、酱油5克", ["高血压"])
        self.assertTrue(violations)
        self.assertTrue(all(v.get("detail") for v in violations),
                        "数值层接管后应全部是带账单的数值条")

    def test_salt_without_number_stays_word_level(self):
        violations = audit("用低钠盐替代调味", ["高血压"])
        self.assertTrue(any(v["keyword"] == "盐" for v in violations))

    def test_capped_numeric_mention_is_not_flagged(self):
        # 「限制语 + 数字」双条件豁免合规表述（E4 lipid 残留根因）
        self.assertEqual(audit("反式脂肪控制在2克以内", ["高脂血症"]), [])
        self.assertEqual(audit("少吃反式脂肪，每日不超过2克", ["高脂血症"]), [])

    def test_numeric_mention_without_limit_language_still_flagged(self):
        # 缺限制语不豁免：「一天吃4克」是摄入描述不是限制表述
        violations = audit("油炸食品含反式脂肪，一天吃4克", ["高脂血症"])
        self.assertTrue(any(v["keyword"] == "反式脂肪" for v in violations))

    def test_pregnancy_strict_wording_not_exempt_by_weak_reduction(self):
        # 保守立场：「少吃生鱼」是弱化表述，孕期生食应完全避免——仍提示
        violations = audit("避免生冷和生食，少吃生鱼", ["孕期"])
        self.assertEqual(len(violations), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
