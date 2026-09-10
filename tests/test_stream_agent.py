import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

import main


class TestToolBudgetStream(unittest.TestCase):
    def test_finalize_message_is_forwarded_as_token(self):
        def fake_stream(*args, **kwargs):
            yield (
                "updates",
                {
                    "tool_budget_finalize": {
                        "messages": [AIMessage(content="已基于当前检索结果完成收口。")]
                    }
                },
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="推荐一道番茄炒蛋"), "test-thread"))

        self.assertIn(("token", "已基于当前检索结果完成收口。"), events)


class TestImageTextProxy(unittest.TestCase):
    @patch("main.describe_image", return_value="可见鸡蛋 3 个、青菜一把。")
    def test_image_is_converted_to_text_for_text_only_brain(self, _mock_describe):
        message = main.build_human_message(
            "晚餐吃什么",
            "https://example.com/fridge.jpg",
        )

        self.assertIsInstance(message.content, str)
        self.assertIn("视觉模型识别结果", message.content)
        self.assertIn("鸡蛋 3 个", message.content)

    @patch("main.describe_image", side_effect=RuntimeError("vision unavailable"))
    def test_vision_failure_degrades_to_clear_text_request(self, _mock_describe):
        message = main.build_human_message(
            "晚餐吃什么",
            "https://example.com/fridge.jpg",
        )

        self.assertIsInstance(message.content, str)
        self.assertIn("图片识别未完成", message.content)
        self.assertIn("告诉我图片里的食材和数量", message.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
