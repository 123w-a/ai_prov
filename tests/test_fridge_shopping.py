"""冰箱库存与采购清单的确定性回归测试。"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from api.routes import fridge_route
from api.routes.shopping_route import shopping_list


class FridgeInventoryTest(unittest.TestCase):
    def run_with_test_file(self, callback):
        path = Path(__file__).with_name(".fridge-test.json")
        try:
            with patch.object(fridge_route, "_FILE", path):
                return callback(path)
        finally:
            path.unlink(missing_ok=True)

    def test_set_add_get_preserves_and_deduplicates_items(self):
        def exercise(path):
            self.assertEqual(fridge_route.set_fridge("鸡蛋,西兰花")["items"], ["鸡蛋", "西兰花"])
            result = fridge_route.add_fridge("番茄,鸡蛋")

            self.assertEqual(result["items"], ["鸡蛋", "西兰花", "番茄"])
            self.assertEqual(fridge_route.get_fridge()["items"], result["items"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"items": result["items"]})

        self.run_with_test_file(exercise)

    def test_add_empty_input_does_not_change_inventory(self):
        def exercise(_path):
            fridge_route.set_fridge("鸡蛋,西兰花")
            self.assertEqual(fridge_route.add_fridge("")["items"], ["鸡蛋", "西兰花"])

        self.run_with_test_file(exercise)


class ShoppingListTest(unittest.TestCase):
    def test_owned_persisted_ingredients_are_deducted(self):
        result = shopping_list("番茄炒蛋", "鸡蛋,食用油")

        self.assertNotIn("鸡蛋", result["main"])
        self.assertNotIn("食用油", result["seasoning"])
        self.assertIn("番茄", result["main"])

    def test_duplicate_dishes_do_not_duplicate_shopping_items(self):
        result = shopping_list("番茄炒蛋,番茄炒蛋", "")

        self.assertEqual(len(result["main"]), len(set(result["main"])))
        self.assertEqual(len(result["seasoning"]), len(set(result["seasoning"])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
