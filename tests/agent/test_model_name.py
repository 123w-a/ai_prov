import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from infrastructure.model_name import (
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
        """provider=qwen 一定走 OpenAI 兼容适配器，绝不落到 ChatDeepSeek。

        只钉**适配器类型**，不钉型号名：型号由 `.env` 的 `VISION_MODEL_NAME` 决定，
        而它在 `configs.py` 的 import 阶段就被固化进 `VISION_CONFIGS` —— `patch.dict`
        改不动一个已经算好的字典，`get_vision_llm` 里那句动态读取又只在参数为 None
        时才兜底。所以钉型号名等于在断言"本机 .env 长什么样"，换台机器就红
        （2026-10-14 实测：本机设的是 qwen3-vl-plus，这条断言红了，而功能是好的）。
        型号那一层改由下面那条「参数优先」来钉。"""
        model = get_vision_llm()

        self.assertIsInstance(model, ChatOpenAI)
        self.assertNotIsInstance(model, ChatDeepSeek)
        # 视觉模型名以 .env 的 VISION_MODEL_NAME 为准（可随成本/能力调整），
        # 此处只校验仍为 Qwen 系且已成功解析，不锁死具体版本号。
        self.assertTrue(
            model.model_name.startswith("qwen"),
            f"视觉模型应为 Qwen 系，实际为 {model.model_name!r}",
        )

    def test_vision_model_name_can_be_overridden_per_call(self):
        """显式传 model_name 时以参数为准（调用期入参优先于 import 期固化值）。"""
        model = get_vision_llm(model_name="vision-explicit-fixture")

        self.assertEqual(model.model_name, "vision-explicit-fixture")


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
