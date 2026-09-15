import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

import main


class TestControlJsonGate(unittest.TestCase):
    """流式控制 JSON 门闸：正常正文零延迟放行，JSON 整条不外发。"""

    def _drain(self, chunks):
        """把 chunks 依次喂进 _stream_agent，返回外发的 token 拼接结果。"""

        def fake_stream(*args, **kwargs):
            for text in chunks:
                yield (
                    "messages",
                    (AIMessageChunk(content=text, id="m1"), {"langgraph_node": "chef_think"}),
                )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="推荐一道菜"), "gate-thread"))
        return "".join(p for kind, p in events if kind == "token")

    def test_plain_prose_streams_through_untouched(self):
        out = self._drain(["番茄", "炒蛋", "很简单"])
        self.assertEqual(out, "番茄炒蛋很简单")

    def test_fenced_control_json_is_fully_suppressed(self):
        # 复现真实故障：模型把整包 ChefAnswer JSON（带 ```json 围栏）当正文流式吐出。
        chunks = [
            "```json\n",
            '{\n  "opening": ',
            '"好的，这就给你做。"',
            ',\n  "recipes": [',
            '{"name": "番茄炒蛋"}',
            "]\n}\n",
            "```",
        ]
        out = self._drain(chunks)
        self.assertEqual(out, "")
        self.assertNotIn("recipes", out)
        self.assertNotIn("```", out)

    def test_bare_control_json_is_fully_suppressed(self):
        chunks = ['{"answer_kind": ', '"recipe", ', '"candidates": ', '["番茄炒蛋"]}']
        self.assertEqual(self._drain(chunks), "")

    def test_brace_start_without_control_key_is_not_swallowed(self):
        # 正文里贴 JSON 示例（以 { 开头但没有控制键）：扣过探测窗口后必须补发，不吞正文。
        body = "{" + "示例内容" * 120  # 远超探测窗口，且不含任何控制键
        out = self._drain([body[:10], body[10:]])
        self.assertEqual(out, body)

    def test_pending_text_is_flushed_when_message_ends(self):
        # 被扣住但未判定为 JSON：节点结束时必须补发，避免整段正文消失。
        def fake_stream(*args, **kwargs):
            yield (
                "messages",
                (AIMessageChunk(content="{示例短文本", id="m1"), {"langgraph_node": "chef_think"}),
            )
            yield ("updates", {"chef_think": {"messages": [AIMessage(content="x")]}})

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="推荐一道菜"), "gate-thread"))
        self.assertIn(("token", "{示例短文本"), events)

    def test_gate_resets_between_messages(self):
        # 上一条被判为控制 JSON 后，下一条正常正文不能跟着被吞掉。
        gate = main._ControlJsonGate()
        gate.feed('{"recipes": [')
        self.assertEqual(gate.feed("]}"), "")
        fresh = main._ControlJsonGate()
        self.assertEqual(fresh.feed("正常回答"), "正常回答")


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
