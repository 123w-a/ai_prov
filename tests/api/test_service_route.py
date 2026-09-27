"""上门私厨预览接口的确定性测试。

这里不测图片识别、语音识别和真实下单，因为它们是明确未实现能力；
测试重点是：文字食材解析、缺口计算、能力边界是否诚实返回。
"""

import unittest

from api.routes.service_route import (
    ServicePreviewRequest,
    _build_preview,
    _split_inventory_text,
)


class SplitInventoryTextTest(unittest.TestCase):
    def test_removes_quantity_and_prefix(self):
        tokens = _split_inventory_text("鸡蛋2个、番茄、家里还有青椒")
        self.assertEqual(tokens, ["鸡蛋", "番茄", "青椒"])


class HomeChefPreviewTest(unittest.TestCase):
    def test_missing_ingredients_for_qingjiao_beef(self):
        payload = ServicePreviewRequest(
            recipe_name="青椒牛肉",
            inventory_text="鸡蛋2个、番茄、家里还有青椒",
            image_url="https://oss.example.com/1.jpg",
        )
        data = _build_preview(payload)

        self.assertEqual(data["detected_from_text"], ["鸡蛋", "番茄", "青椒"])
        self.assertIn("牛肉", data["missing_ingredients"])
        self.assertIn("洋葱", data["missing_ingredients"])
        self.assertIn("生抽", data["missing_ingredients"])
        self.assertEqual(data["chef_can_bring"], data["missing_ingredients"])

        self.assertTrue(data["image_received"])
        self.assertFalse(data["image_recognition_supported"])
        self.assertFalse(data["order_supported"])
        self.assertEqual(data["status"], "demo")

    def test_voice_preview_is_explicitly_unsupported(self):
        payload = ServicePreviewRequest(
            recipe_name="青椒牛肉",
            inventory_text="",
            mode="voice",
        )
        data = _build_preview(payload)

        self.assertTrue(data["voice_input_received"])
        self.assertTrue(data["voice_input_supported"])
        self.assertIsInstance(data["voice_recognition_available"], bool)
        self.assertEqual(data["voice_recognition_route"], "/api/transcribe")
        self.assertFalse(data["voice_to_service_integrated"])
        self.assertIn("/api/transcribe 已接入", data["voice_status"])


if __name__ == "__main__":
    unittest.main()