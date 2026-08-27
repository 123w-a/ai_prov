# tests/test_nutrition_rules.py
# 硬护栏规则引擎单测：验证 L3 确定性审计（不依赖 LLM）
import unittest
from nutrition_rules import detect_conditions, audit, describe, RULES, SODIUM_SENSITIVE


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
