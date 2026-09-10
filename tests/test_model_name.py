import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from model_name import (
    PatchedChatDeepSeek,
    extract_message_text,
    get_langchain_llm,
    get_summary_llm,
    get_vision_llm,
)


class ModelProviderTest(unittest.TestCase):
    def test_deepseek_uses_native_adapter(self):
        model = get_langchain_llm("deepseek")

        self.assertIsInstance(model, ChatDeepSeek)

    def test_other_providers_keep_openai_compatible_adapter(self):
        model = get_langchain_llm("gpt")

        self.assertIsInstance(model, ChatOpenAI)
        self.assertNotIsInstance(model, ChatDeepSeek)

    def test_vision_defaults_to_qwen_openai_compatible_adapter(self):
        model = get_vision_llm()

        self.assertIsInstance(model, ChatOpenAI)
        self.assertNotIsInstance(model, ChatDeepSeek)
        self.assertEqual(model.model_name, "qwen3-vl-235b-a22b-instruct")


class SummaryModelTest(unittest.TestCase):
    def test_deepseek_summary_defaults_to_chat(self):
        with patch.dict(os.environ, {"SUMMARY_MODE_NAME": ""}):
            model = get_summary_llm("deepseek")

        self.assertEqual(model.model_name, "deepseek-chat")

    def test_summary_model_name_can_be_overridden(self):
        with patch.dict(os.environ, {"SUMMARY_MODE_NAME": "deepseek-v4-flash"}):
            model = get_summary_llm("deepseek")

        self.assertEqual(model.model_name, "deepseek-v4-flash")


class MessageTextTest(unittest.TestCase):
    def test_uses_content_when_present(self):
        response = SimpleNamespace(content="  正常正文  ", additional_kwargs={"reasoning_content": "思维链"})

        self.assertEqual(extract_message_text(response), "正常正文")

    def test_uses_reasoning_only_when_fallback_is_enabled(self):
        response = SimpleNamespace(content="", additional_kwargs={"reasoning_content": " 降级摘要 "})

        self.assertEqual(extract_message_text(response), "")
        self.assertEqual(extract_message_text(response, allow_reasoning_fallback=True), "降级摘要")

    def test_joins_content_blocks(self):
        response = SimpleNamespace(content=[{"text": "本周你"}, {"text": "记录 2 餐"}])

        self.assertEqual(extract_message_text(response), "本周你记录 2 餐")

    def test_returns_empty_when_both_are_missing(self):
        response = SimpleNamespace(content=None, additional_kwargs={})

        self.assertEqual(extract_message_text(response), "")


class DeepSeekReasoningEchoTest(unittest.TestCase):
    def test_reasoning_content_is_put_back_on_outbound_assistant(self):
        model = PatchedChatDeepSeek(
            model="deepseek-flash",
            api_key="test",
            base_url="https://api.deepseek.com",
        )
        messages = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "web_search",
                        "args": {"query": "番茄炒蛋"},
                    }
                ],
                additional_kwargs={"reasoning_content": "<思考链内容>"},
            ),
            ToolMessage(content="ok", name="web_search", tool_call_id="c1"),
        ]

        payload = model._get_request_payload(messages)
        assistant = next(
            message for message in payload["messages"] if message.get("role") == "assistant"
        )

        self.assertEqual(assistant["reasoning_content"], "<思考链内容>")
        self.assertEqual(payload["messages"][-1]["role"], "tool")

    def test_empty_reasoning_content_is_echoed_for_tool_call_history(self):
        model = PatchedChatDeepSeek(
            model="deepseek-flash",
            api_key="test",
            base_url="https://api.deepseek.com",
        )
        messages = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "web_search",
                        "args": {"query": "番茄炒蛋"},
                    }
                ],
            ),
            ToolMessage(content="ok", name="web_search", tool_call_id="c1"),
        ]

        payload = model._get_request_payload(messages)
        assistant = next(
            message for message in payload["messages"] if message.get("role") == "assistant"
        )

        self.assertEqual(assistant["reasoning_content"], "")

    def test_assistant_without_tool_calls_does_not_get_reasoning_content(self):
        model = PatchedChatDeepSeek(
            model="deepseek-flash",
            api_key="test",
            base_url="https://api.deepseek.com",
        )
        messages = [AIMessage(content="你好")]

        payload = model._get_request_payload(messages)
        assistant = next(
            message for message in payload["messages"] if message.get("role") == "assistant"
        )

        self.assertNotIn("reasoning_content", assistant)


if __name__ == "__main__":
    unittest.main()
