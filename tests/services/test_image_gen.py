"""AI 菜品生图提示词、质量验收与缓存版本回归。"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services import image_gen


class DishImagePromptTest(unittest.TestCase):
    def test_enoki_prompt_contains_shape_and_error_negative_words(self):
        dish = "剁椒蒸金针菇（少辣版）"

        prompt = image_gen.build_image_prompt(dish)
        negative = image_gen.build_negative_prompt(dish)

        self.assertIn("细长", prompt)
        self.assertIn("细小圆菌盖", prompt)
        self.assertIn("根根分明", prompt)
        self.assertIn("不要整把直立成圆柱花束", prompt)
        self.assertIn("红亮辣椒碎", prompt)
        self.assertIn("第一优先级是让人一眼就想吃", prompt)
        self.assertIn("剁椒必须红亮、湿润、颗粒清晰", prompt)
        self.assertIn("鱿鱼圈", negative)
        self.assertIn("杏鲍菇厚片", negative)
        self.assertIn("宽粉", negative)
        self.assertIn("剁椒发黑", negative)

    def test_strict_prompt_has_explicit_correction_guidance(self):
        prompt = image_gen.build_image_prompt("金针菇", strict=True)
        negative = image_gen.build_negative_prompt("金针菇", strict=True)

        self.assertIn("纠错重生成", prompt)
        self.assertIn("形状与菜名不符", negative)

    def test_retry_prompt_contains_previous_visual_problems(self):
        prompt = image_gen.build_image_prompt(
            "金针菇",
            strict=True,
            retry_problems=["像鱿鱼圈", "食材变成杏鲍菇厚片"],
        )
        negative = image_gen.build_negative_prompt(
            "金针菇",
            strict=True,
            retry_problems=["像鱿鱼圈", "食材变成杏鲍菇厚片"],
        )

        self.assertIn("像鱿鱼圈", prompt)
        self.assertIn("必须避免重复", prompt)
        self.assertIn("上一图问题：像鱿鱼圈；食材变成杏鲍菇厚片", negative)

    def test_rice_noodles_use_noodle_rule_without_fan_rule_conflict(self):
        prompt = image_gen.build_image_prompt("炒米粉")

        self.assertIn("面条要呈细长", prompt)
        self.assertNotIn("粉丝或粉条必须", prompt)

    def test_ambiguous_names_do_not_trigger_wrong_ingredient_rules(self):
        fish_fragrant_eggplant = image_gen.build_image_prompt("鱼香茄子")
        fish_fragrant_pork = image_gen.build_image_prompt("鱼香肉丝")
        king_oyster_mushroom = image_gen.build_image_prompt("鸡腿菇炒肉")

        self.assertNotIn("鱼应保留明确的鱼皮", fish_fragrant_eggplant)
        self.assertNotIn("鱼应保留明确的鱼皮", fish_fragrant_pork)
        self.assertIn("肉类要按菜名呈现正确形态", fish_fragrant_pork)
        self.assertNotIn("肉类要按菜名呈现正确形态", king_oyster_mushroom)
        self.assertIn("菌菇应呈现真实菌盖", king_oyster_mushroom)


class DishImageGenerationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_path = str(Path(self.tmp.name) / "dish_image_cache.json")

    def _generate(self, assessment, *, dish="金针菇"):
        with patch.object(image_gen, "CACHE_PATH", self.cache_path), \
             patch.object(image_gen, "DASHSCOPE_API_KEY", "test-key"), \
             patch.object(image_gen, "_request_wanx_image", return_value="https://wanx/image.png") as request, \
             patch.object(
                 image_gen,
                 "_download_generated_image",
                 return_value=(b"first-image", "image/jpeg"),
             ), \
             patch.object(image_gen, "_audit_generated_dish_image", return_value=assessment), \
             patch.object(image_gen, "upload_to_oss", return_value="https://oss/first.png"):
            url = image_gen.generate_dish_image(dish)
        return url, request

    def test_legacy_cache_key_remains_compatible(self):
        Path(self.cache_path).write_text(
            json.dumps({"金针菇": "https://oss/legacy.png"}, ensure_ascii=False),
            encoding="utf-8",
        )

        with patch.object(image_gen, "CACHE_PATH", self.cache_path):
            self.assertEqual(
                image_gen.cached_dish_image("金针菇"),
                "https://oss/legacy.png",
            )

    def test_good_first_image_is_uploaded_without_retry(self):
        assessment = {
            "dish_name_match": True,
            "ingredient_shape_match": True,
            "appeal_score": 4.5,
            "problems": [],
        }

        # 在 helper 外覆盖上传地址，验证返回和缓存键。
        with patch.object(image_gen, "CACHE_PATH", self.cache_path), \
             patch.object(image_gen, "DASHSCOPE_API_KEY", "test-key"), \
             patch.object(image_gen, "_request_wanx_image", return_value="https://wanx/image.png") as request, \
             patch.object(
                 image_gen,
                 "_download_generated_image",
                 return_value=(b"first-image", "image/jpeg"),
             ), \
             patch.object(image_gen, "_audit_generated_dish_image", return_value=assessment), \
             patch.object(image_gen, "upload_to_oss", return_value="https://oss/first.png"):
            url = image_gen.generate_dish_image("金针菇")

        self.assertEqual(url, "https://oss/first.png")
        request.assert_called_once()
        cached = json.loads(Path(self.cache_path).read_text(encoding="utf-8"))
        self.assertEqual(cached["v4:金针菇"], "https://oss/first.png")

    def test_bad_first_image_triggers_one_strict_regeneration(self):
        bad = {
            "dish_name_match": True,
            "ingredient_shape_match": False,
            "appeal_score": 2.0,
            "problems": ["像鱿鱼圈"],
        }
        good = {
            "dish_name_match": True,
            "ingredient_shape_match": True,
            "appeal_score": 4.2,
            "problems": [],
        }

        with patch.object(image_gen, "CACHE_PATH", self.cache_path), \
             patch.object(image_gen, "DASHSCOPE_API_KEY", "test-key"), \
             patch.object(
                 image_gen,
                 "_request_wanx_image",
                 side_effect=["https://wanx/first.png", "https://wanx/second.png"],
             ) as request, \
             patch.object(
                 image_gen,
                 "_download_generated_image",
                 side_effect=[
                     (b"bad-image", "image/jpeg"),
                     (b"good-image", "image/jpeg"),
                 ],
             ), \
             patch.object(
                 image_gen,
                 "_audit_generated_dish_image",
                 side_effect=[bad, good],
             ), \
             patch.object(image_gen, "upload_to_oss", return_value="https://oss/second.png"):
            url = image_gen.generate_dish_image("金针菇")

        self.assertEqual(url, "https://oss/second.png")
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("纠错重生成", request.call_args_list[0].args[0])
        self.assertIn("纠错重生成", request.call_args_list[1].args[0])
        self.assertIn("像鱿鱼圈", request.call_args_list[1].args[0])
        self.assertIn("像鱿鱼圈", request.call_args_list[1].args[1])
        cached = json.loads(Path(self.cache_path).read_text(encoding="utf-8"))
        self.assertEqual(cached["v4:金针菇"], "https://oss/second.png")

    def test_quality_model_failure_is_fail_open(self):
        url, request = self._generate(None)

        self.assertEqual(url, "https://oss/first.png")
        request.assert_called_once()

    def test_call_with_timeout_does_not_wait_for_slow_worker(self):
        started = threading.Event()
        release = threading.Event()

        def slow_call():
            started.set()
            release.wait(2)
            return "late"

        try:
            before = time.monotonic()
            with self.assertRaises(TimeoutError):
                image_gen._call_with_timeout(slow_call, 0.02)
            elapsed = time.monotonic() - before

            self.assertTrue(started.wait(0.2))
            self.assertLess(elapsed, 0.5)
        finally:
            release.set()

    def test_unparseable_quality_response_fails_open_without_retry(self):
        class FakeQualityLLM:
            def invoke(self, messages):
                return type("Response", (), {"content": "不是 JSON"})()

        with patch.object(image_gen, "_IMAGE_QUALITY_LLM", FakeQualityLLM()):
            assessment = image_gen._audit_generated_dish_image(
                "金针菇",
                b"fake-image",
                "image/jpeg",
            )

        self.assertIsNone(assessment)
        self.assertTrue(image_gen._quality_passed(assessment))

    def test_two_wrong_dishes_fall_back_to_first_image_and_cache(self):
        wrong = {
            "dish_name_match": False,
            "ingredient_shape_match": False,
            "appeal_score": 2.0,
            "problems": ["完全不同的菜"],
        }

        with patch.object(image_gen, "CACHE_PATH", self.cache_path), \
             patch.object(image_gen, "DASHSCOPE_API_KEY", "test-key"), \
             patch.object(
                 image_gen,
                 "_request_wanx_image",
                 side_effect=["https://wanx/first.png", "https://wanx/second.png"],
             ) as request, \
             patch.object(
                 image_gen,
                 "_download_generated_image",
                 side_effect=[
                     (b"wrong-one", "image/jpeg"),
                     (b"wrong-two", "image/jpeg"),
                 ],
             ), \
             patch.object(
                 image_gen,
                 "_audit_generated_dish_image",
                 side_effect=[wrong, wrong],
             ), \
             patch.object(
                 image_gen,
                 "upload_to_oss",
                 return_value="https://oss/wrong.png",
             ) as upload:
            url = image_gen.generate_dish_image("金针菇")

        self.assertEqual(url, "https://oss/wrong.png")
        self.assertEqual(request.call_count, 2)
        upload.assert_called_once()
        cached = json.loads(Path(self.cache_path).read_text(encoding="utf-8"))
        self.assertEqual(cached["v4:金针菇"], "https://oss/wrong.png")


if __name__ == "__main__":
    unittest.main(verbosity=2)
