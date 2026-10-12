import unittest
import json
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

    def test_finalize_structured_payload_is_forwarded_as_answer(self):
        payload = {
            "opening": "已完成安全收口。",
            "recipes": [],
            "guardrails": [],
        }

        def fake_stream(*args, **kwargs):
            yield (
                "updates",
                {
                    "tool_budget_finalize": {
                        "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))]
                    }
                },
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="推荐一道菜"), "test-thread"))

        self.assertIn(("answer", payload), events)
        self.assertNotIn(("token", json.dumps(payload, ensure_ascii=False)), events)

    def test_suppressed_control_json_is_recovered_when_no_other_output_exists(self):
        payload = {
            "opening": "已根据你的约束整理好候选。",
            "answer_kind": "recipe",
            "recipes": [{"name": "番茄鸡胸肉"}],
        }

        def fake_stream(*args, **kwargs):
            raw = json.dumps(payload, ensure_ascii=False)
            yield (
                "messages",
                (AIMessageChunk(content=raw, id="m1"), {"langgraph_node": "chef_think"}),
            )
            yield (
                "updates",
                {"chef_think": {"messages": [AIMessage(content=raw)]}},
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="推荐一道菜"), "fallback-thread"))

        self.assertIn(("answer", payload), events)


class TestVerifyRetryStream(unittest.TestCase):
    """护栏打回重生成：必须外发 reset，让下游丢掉上一版正文。

    复现背景（2026-10-10 会话 user_c9b101adf6）：chef_think 第一次生成的正文
    已经流式推给前端，verify_answer 判 retry 后 chef_think 又生成一次，
    两版被首尾拼在一起 ⇒ 用户在同一轮里看到同一道菜讲两遍。
    """

    def test_retry_emits_reset_between_generations(self):
        def fake_stream(*args, **kwargs):
            yield (
                "messages",
                (AIMessageChunk(content="第一版正文", id="m1"), {"langgraph_node": "chef_think"}),
            )
            yield (
                "updates",
                {"verify_answer": {"verify_status": "retry", "verify_attempts": 1}},
            )
            yield (
                "messages",
                (AIMessageChunk(content="第二版正文", id="m2"), {"langgraph_node": "chef_think"}),
            )
            yield (
                "updates",
                {"chef_think": {"messages": [AIMessage(content="第二版正文")]}},
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(
                main._stream_agent(HumanMessage(content="做3号"), "retry-thread")
            )

        kinds = [kind for kind, _ in events]
        self.assertIn("reset", kinds)
        self.assertEqual([p for kind, p in events if kind == "token"],
                         ["第一版正文", "第二版正文"])
        # reset 必须精确落在两版之间，才能让下游只保留最后一版
        self.assertEqual(kinds.index("reset"), kinds.index("token") + 1)

    def test_reset_is_not_a_displayable_output(self):
        # reset 是控制事件：不能顶替「零输出兜底」的判定依据。
        def fake_stream(*args, **kwargs):
            yield (
                "updates",
                {"verify_answer": {"verify_status": "retry", "verify_attempts": 1}},
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(main._stream_agent(HumanMessage(content="做3号"), "retry-only"))
        self.assertEqual([kind for kind, _ in events], ["reset"])

    def test_ok_status_emits_no_reset(self):
        def fake_stream(*args, **kwargs):
            yield (
                "messages",
                (AIMessageChunk(content="正文", id="m1"), {"langgraph_node": "chef_think"}),
            )
            yield (
                "updates",
                {"verify_answer": {"verify_status": "ok", "verify_attempts": 1}},
            )

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            events = list(
                main._stream_agent(HumanMessage(content="推荐一道菜"), "ok-thread")
            )
        self.assertNotIn("reset", [kind for kind, _ in events])


class TestStructuredOpeningStream(unittest.TestCase):
    def _events(self, *stream_items):
        def fake_stream(*args, **kwargs):
            yield from stream_items

        with patch.object(main.agent, "stream", side_effect=fake_stream):
            return list(
                main._stream_agent(HumanMessage(content="做2号"), "opening-stream")
            )

    def test_structured_opening_streams_when_no_visible_token_exists(self):
        payload = {
            "opening": "这道菜先处理食材，再按顺序下锅，完整讲解如下。",
            "answer_kind": "recipe",
            "recipes": [{"name": "青菜豆腐汤"}],
        }
        events = self._events(
            (
                "updates",
                {
                    "structure_answer": {
                        "messages": [
                            AIMessage(content=json.dumps(payload, ensure_ascii=False))
                        ]
                    }
                },
            )
        )

        tokens = [item for kind, item in events if kind == "token"]
        self.assertEqual("".join(tokens), payload["opening"])
        self.assertGreater(len(tokens), 1)
        self.assertIn(("answer", json.dumps(payload, ensure_ascii=False)), events)

    def test_structured_opening_is_not_repeated_after_visible_tokens(self):
        payload = {
            "opening": "这道菜先处理食材，再按顺序下锅，完整讲解如下。",
            "answer_kind": "recipe",
            "recipes": [{"name": "青菜豆腐汤"}],
        }
        events = self._events(
            (
                "messages",
                (
                    AIMessageChunk(content="已经流式显示过的正文", id="m1"),
                    {"langgraph_node": "chef_think"},
                ),
            ),
            (
                "updates",
                {
                    "structure_answer": {
                        "messages": [
                            AIMessage(content=json.dumps(payload, ensure_ascii=False))
                        ]
                    }
                },
            ),
        )

        tokens = [item for kind, item in events if kind == "token"]
        self.assertEqual(tokens, ["已经流式显示过的正文"])

    def test_candidate_payload_does_not_stream_as_recipe_opening(self):
        payload = {
            "opening": "1. 青菜豆腐汤\n2. 番茄炒蛋",
            "answer_kind": "candidates",
            "candidates": ["青菜豆腐汤", "番茄炒蛋"],
            "recipes": [],
        }
        events = self._events(
            (
                "updates",
                {
                    "structure_answer": {
                        "messages": [
                            AIMessage(content=json.dumps(payload, ensure_ascii=False))
                        ]
                    }
                },
            )
        )

        self.assertFalse(any(kind == "token" for kind, _ in events))

    def test_reset_clears_visible_flag_before_structured_opening(self):
        payload = {
            "opening": "第二版安全完整讲解，按顺序操作即可。",
            "answer_kind": "recipe",
            "recipes": [{"name": "清蒸鲈鱼"}],
        }
        events = self._events(
            (
                "messages",
                (
                    AIMessageChunk(content="第一版待丢弃正文", id="m1"),
                    {"langgraph_node": "chef_think"},
                ),
            ),
            (
                "updates",
                {"verify_answer": {"verify_status": "retry", "verify_attempts": 1}},
            ),
            (
                "updates",
                {
                    "structure_answer": {
                        "messages": [
                            AIMessage(content=json.dumps(payload, ensure_ascii=False))
                        ]
                    }
                },
            ),
        )

        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds.count("reset"), 1)
        tokens = [item for kind, item in events if kind == "token"]
        self.assertEqual(tokens[0], "第一版待丢弃正文")
        self.assertEqual("".join(tokens[1:]), payload["opening"])
        self.assertGreater(len(tokens[1:]), 1)


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
