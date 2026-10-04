# tests/domain/test_dish_nutrition.py
# 「这顿营养数值」确定性计算单测：查表累加、缺失判定、绝不给缺失项填 0。
#
# 期望值全部用 100g 整倍用量：per100 × 1 就是国标表原值，可以手算核对。
# 不用 50g 这类会产生 x.x5 的用量——round(2.05, 1) 在浮点下是 2.0 不是 2.1，
# 那测的是浮点表现，不是这个模块的行为。
import unittest
from unittest.mock import patch

from domain import dish_nutrition
from domain.dish_nutrition import compute_nutrition_facts, lookup_food


class TestLookup(unittest.TestCase):
    """口语名 → 国标表条目。错配比未收录更坏，所以重点测「不该命中什么」。"""

    def test_exact_name(self):
        self.assertEqual(lookup_food("西兰花（绿菜花）")["name"], "西兰花（绿菜花）")

    def test_alias(self):
        self.assertEqual(lookup_food("米饭")["name"], "大米（粳米）")
        self.assertEqual(lookup_food("西红柿")["name"], "番茄（西红柿）")
        self.assertEqual(lookup_food("食用油")["name"], "大豆油")

    def test_stem_fallback(self):
        # 主名唯一时才允许回退（这里「胡萝卜」在表里就叫「胡萝卜」）
        self.assertEqual(lookup_food("胡萝卜")["name"], "胡萝卜")

    def test_no_substring_matching(self):
        # 子串匹配会把这些错配成「猪肉（肥瘦）」「牛肉（瘦）」「面条（挂面）」，那是确定地写错。
        self.assertIsNone(lookup_food("肉"))
        self.assertIsNone(lookup_food("牛"))
        self.assertIsNone(lookup_food("条"))

    def test_real_word_is_still_found(self):
        # 「猪油」是表里真有的条目（猪油（炼））——不许因为怕错配就把真的也挡住
        self.assertEqual(lookup_food("猪油")["name"], "猪油（炼）")

    def test_ambiguous_stem_is_refused(self):
        # 主名重复时 _BY_STEM 必须留 None，禁止"取第一条"
        self.assertIsNone(dish_nutrition._BY_STEM["豆腐"])
        self.assertIsNone(dish_nutrition._BY_STEM["面条"])
        self.assertIsNotNone(dish_nutrition._BY_STEM["胡萝卜"])

    def test_blank_and_unknown(self):
        self.assertIsNone(lookup_food(""))
        self.assertIsNone(lookup_food("   "))
        self.assertIsNone(lookup_food("黑松露"))


class TestCompute(unittest.TestCase):
    """查表累加与三档状态。"""

    def test_single_ingredient_100g(self):
        # 大米（粳米）per100：347 / 7.7 / 0.6 / 77.4 / 0.6 / 2.4
        f = compute_nutrition_facts([{"name": "大米（粳米）", "amount_g": 100}])
        self.assertEqual(f["status"], "complete")
        self.assertEqual(f["nutrients"]["energy_kcal"], {"value": 347.0, "status": "known"})
        self.assertEqual(f["nutrients"]["protein_g"], {"value": 7.7, "status": "known"})
        self.assertEqual(f["nutrients"]["fat_g"], {"value": 0.6, "status": "known"})
        self.assertEqual(f["nutrients"]["carb_g"], {"value": 77.4, "status": "known"})
        self.assertEqual(f["nutrients"]["fiber_g"], {"value": 0.6, "status": "known"})
        self.assertEqual(f["nutrients"]["sodium_mg"], {"value": 2.4, "status": "known"})
        self.assertEqual(f["covered"], ["大米（粳米）"])
        self.assertEqual(f["missing"], [])
        self.assertEqual(f["no_amount"], [])

    def test_two_ingredients_sum(self):
        # 100g 大米 + 100g 鸡蛋（红皮，156/12.8/11.1/1.3/0/125.7）
        f = compute_nutrition_facts([
            {"name": "米饭", "amount_g": 100},
            {"name": "鸡蛋", "amount_g": 100},
        ])
        self.assertEqual(f["status"], "complete")
        n = f["nutrients"]
        self.assertEqual(n["energy_kcal"]["value"], 503.0)
        self.assertEqual(n["protein_g"]["value"], 20.5)
        self.assertEqual(n["fat_g"]["value"], 11.7)
        self.assertEqual(n["carb_g"]["value"], 78.7)
        self.assertEqual(n["fiber_g"]["value"], 0.6)   # 鸡蛋纤维是 0，不是空
        self.assertEqual(n["sodium_mg"]["value"], 128.1)

    def test_ratio_200g(self):
        # 西兰花 per100：36 / 4.1 / 0.6 / 4.3 / 1.6 / 18.8，×2
        f = compute_nutrition_facts([{"name": "西兰花", "amount_g": 200}])
        n = f["nutrients"]
        self.assertEqual(n["energy_kcal"]["value"], 72.0)
        self.assertEqual(n["protein_g"]["value"], 8.2)
        self.assertEqual(n["fat_g"]["value"], 1.2)
        self.assertEqual(n["carb_g"]["value"], 8.6)
        self.assertEqual(n["fiber_g"]["value"], 3.2)
        self.assertEqual(n["sodium_mg"]["value"], 37.6)

    def test_amount_string_forms(self):
        for raw in ("200g", "200 克", "约200克", "200", 200, 200.0):
            with self.subTest(raw=raw):
                f = compute_nutrition_facts([{"name": "西兰花", "amount_g": raw}])
                self.assertEqual(f["nutrients"]["energy_kcal"]["value"], 72.0)

    def test_unparsable_amount_is_no_amount(self):
        # 「适量」「少许」解析不出克数 → 记为没有克数，绝不当 0 克
        for raw in ("适量", "", "少许", None, 0, -5, True):
            with self.subTest(raw=raw):
                f = compute_nutrition_facts([{"name": "西兰花", "amount_g": raw}])
                self.assertEqual(f["status"], "unavailable")
                self.assertEqual(f["no_amount"], ["西兰花"])
                self.assertEqual(f["ingredients"][0]["status"], "no_amount")


class TestIncomplete(unittest.TestCase):
    """不完整时的三档状态与账目——这是这块最容易骗人的地方。"""

    def test_missing_ingredient_makes_partial(self):
        f = compute_nutrition_facts([
            {"name": "大米（粳米）", "amount_g": 100},
            {"name": "培根", "amount_g": 50},
        ])
        self.assertEqual(f["status"], "partial")
        self.assertEqual(f["missing"], ["培根"])
        self.assertEqual(f["covered"], ["大米（粳米）"])
        # 总量只是「已覆盖食材的小计」，不含培根——页面必须照这个口径写
        self.assertEqual(f["nutrients"]["energy_kcal"]["value"], 347.0)
        rows = {r["name"]: r for r in f["ingredients"]}
        self.assertEqual(rows["培根"]["status"], "missing")
        self.assertIsNone(rows["培根"]["lookup_name"])

    def test_no_amount_makes_partial(self):
        f = compute_nutrition_facts([
            {"name": "西兰花", "amount_g": 200},
            {"name": "豆腐", "amount_g": None},
        ])
        self.assertEqual(f["status"], "partial")
        self.assertEqual(f["no_amount"], ["豆腐"])
        rows = {r["name"]: r for r in f["ingredients"]}
        self.assertEqual(rows["豆腐"]["lookup_name"], "豆腐（北）")   # 别名显式指北豆腐
        self.assertIsNone(rows["豆腐"]["amount_g"])
        self.assertEqual(f["nutrients"]["energy_kcal"]["value"], 72.0)

    def test_nothing_usable_is_unavailable(self):
        for payload in ([], None, [{"name": "  "}]):
            with self.subTest(payload=payload):
                f = compute_nutrition_facts(payload)
                self.assertEqual(f["status"], "unavailable")
                self.assertEqual(f["ingredients"], [])
                self.assertEqual(f["covered"], [])
                for key, cell in f["nutrients"].items():
                    self.assertIsNone(cell["value"], key)
                    self.assertEqual(cell["status"], "unavailable", key)

    def test_unavailable_still_records_why(self):
        # 算不出时也不能只剩一句「无法计算」：页面要能说清是「没收录」还是「没有克数」，
        # 所以 ingredients 这份账目在 unavailable 下同样要保留。
        f = compute_nutrition_facts([{"name": "培根", "amount_g": 50}])
        self.assertEqual(f["status"], "unavailable")
        self.assertEqual(f["missing"], ["培根"])
        self.assertEqual(f["ingredients"], [
            {"name": "培根", "amount_g": 50.0, "lookup_name": None, "status": "missing"},
        ])

        f = compute_nutrition_facts([{"name": "西兰花"}])
        self.assertEqual(f["status"], "unavailable")
        self.assertEqual(f["no_amount"], ["西兰花"])
        self.assertEqual(f["ingredients"], [
            {"name": "西兰花", "amount_g": None,
             "lookup_name": "西兰花（绿菜花）", "status": "no_amount"},
        ])


class TestFieldLevelGaps(unittest.TestCase):
    """字段级缺失：现表 258 条六项齐全，所以这些路径只能打桩造。"""

    def test_missing_nutrient_field_is_not_zero(self):
        # 国标表改版删列、或某食物缺钠时，行为必须是「该项未收录」，而不是悄悄写成 0。
        stub = {"name": "打桩食材", "kcal": 100.0, "protein": 5.0,
                "fat": None, "carb": 10.0, "fiber": None, "sodium": None}
        with patch.dict(dish_nutrition._BY_NAME, {"打桩食材": stub}):
            f = compute_nutrition_facts([{"name": "打桩食材", "amount_g": 100}])
        self.assertEqual(f["status"], "complete")
        self.assertEqual(f["nutrients"]["energy_kcal"], {"value": 100.0, "status": "known"})
        self.assertEqual(f["nutrients"]["protein_g"], {"value": 5.0, "status": "known"})
        self.assertEqual(f["nutrients"]["fat_g"], {"value": None, "status": "unavailable"})
        self.assertEqual(f["nutrients"]["fiber_g"], {"value": None, "status": "unavailable"})
        self.assertEqual(f["nutrients"]["sodium_mg"], {"value": None, "status": "unavailable"})

    def test_empty_table_means_everything_missing(self):
        # 表文件缺失 → 全判未收录，绝不回退到任何内置近似值。
        # 两个索引都要清：_BY_NAME 与 _BY_STEM 是两个各自独立的查表入口，
        # 只清一个的话主名回退仍能命中，等于没造出「表是空的」这个条件。
        with patch.dict(dish_nutrition._BY_NAME, {}, clear=True), \
                patch.dict(dish_nutrition._BY_STEM, {}, clear=True):
            f = compute_nutrition_facts([{"name": "西兰花", "amount_g": 200}])
        self.assertEqual(f["status"], "unavailable")
        self.assertEqual(f["missing"], ["西兰花"])


class TestContract(unittest.TestCase):
    """形状契约：前端按 NUTRIENT_FIELDS 的顺序渲染，不能在这里乱序。"""

    def test_six_rows_in_fixed_order(self):
        f = compute_nutrition_facts([{"name": "西兰花", "amount_g": 200}])
        self.assertEqual(
            list(f["nutrients"].keys()),
            ["energy_kcal", "protein_g", "fat_g", "carb_g", "fiber_g", "sodium_mg"],
        )
        self.assertEqual(
            [(label, unit) for _k, label, unit, _f in dish_nutrition.NUTRIENT_FIELDS],
            [("能量", "kcal"), ("蛋白质", "g"), ("脂肪", "g"),
             ("碳水化合物", "g"), ("膳食纤维", "g"), ("钠", "mg")],
        )
        self.assertEqual(f["basis"], "recipe_ingredient_amounts")

    def test_accepts_attribute_objects(self):
        # 结构层的 Ingredient 是 pydantic 模型（属性访问），不是 dict
        class Ing:
            def __init__(self, name, amount_g):
                self.name = name
                self.amount_g = amount_g

        f = compute_nutrition_facts([Ing("西兰花", 200)])
        self.assertEqual(f["nutrients"]["energy_kcal"]["value"], 72.0)
        self.assertEqual(f["covered"], ["西兰花"])


if __name__ == "__main__":
    unittest.main()
