import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

import vision_service


class VisionServiceTest(unittest.TestCase):
    @patch("vision_service.get_vision_llm")
    def test_describe_image_returns_text_and_includes_user_request(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = AIMessage(
            content="可见豆腐一盒、番茄两个。"
        )

        result = vision_service.describe_image(
            "https://example.com/fridge.jpg",
            "帮我做晚餐",
        )

        self.assertEqual(result, "可见豆腐一盒、番茄两个。")
        message = mock_get_llm.return_value.invoke.call_args.args[0][0]
        prompt = message.content[0]["text"]
        self.assertIn("帮我做晚餐", prompt)
        self.assertEqual(
            message.content[1]["image_url"]["url"],
            "https://example.com/fridge.jpg",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
