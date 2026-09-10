import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dish_assets_store as store
from api.routes import chat_route


class TestDishAssets(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.asset_path = Path(self.temp_dir.name) / "dish_assets.json"
        self.asset_patch = patch.object(store, "ASSETS_PATH", self.asset_path)
        self.asset_patch.start()

    def tearDown(self):
        self.asset_patch.stop()
        self.temp_dir.cleanup()

    def test_asset_survives_a_new_lookup_without_session_context(self):
        recipe = {
            "name": "番茄炒蛋（咸鲜原味）",
            "intro": "酸香下饭",
            "steps": ["炒蛋", "炒番茄"],
            "image_url": "https://oss.example/egg.png",
            "image_ai_generated": True,
            "image_note": "AI 生成示意图",
        }
        self.assertTrue(store.upsert_dish_asset(recipe, "session_a"))

        found = store.find_dish_asset("番茄炒蛋")
        self.assertIsNotNone(found)
        self.assertEqual(found["image_url"], "https://oss.example/egg.png")
        self.assertEqual(found["recipe"]["steps"], ["炒蛋", "炒番茄"])

    def test_global_prompt_does_not_copy_health_conclusion(self):
        store.upsert_dish_asset(
            {
                "name": "番茄炒蛋",
                "intro": "基础做法",
                "image_url": "https://oss.example/egg.png",
            },
            "session_a",
        )
        prompt = chat_route._global_asset_prompt("我想吃番茄炒蛋")
        self.assertIn("跨对话菜品资产参考", prompt)
        self.assertIn("https://oss.example/egg.png", prompt)
        self.assertIn("不能继承来源对话的健康结论", prompt)

    def test_asset_can_be_found_inside_a_natural_sentence(self):
        store.upsert_dish_asset(
            {"name": "番茄炒蛋", "image_url": "https://oss.example/egg.png"},
            "session_a",
        )
        found = store.find_dish_asset_in_text(
            "我想吃番茄炒蛋，给我看看之前的图片，并根据我现在的情况重新推荐"
        )
        self.assertEqual(found["name"], "番茄炒蛋")

    def test_asset_image_can_be_updated_after_async_generation(self):
        store.upsert_dish_asset({"name": "红烧肉", "steps": ["焯水"]}, "session_a")
        self.assertTrue(
            store.update_dish_asset_image(
                "红烧肉",
                "https://oss.example/pork.png",
                image_ai_generated=False,
            )
        )
        found = store.find_dish_asset("红烧肉")
        self.assertEqual(found["recipe"]["image_url"], "https://oss.example/pork.png")


if __name__ == "__main__":
    unittest.main(verbosity=2)
