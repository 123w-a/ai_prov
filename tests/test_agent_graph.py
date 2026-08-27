# tests/test_agent_graph.py
# 测试 agent_graph.py 的纯函数与新增 verify_answer 护栏节点（不触发 LLM 实时调用）
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

# 导入被测模块（其导入链会初始化 LLM/编译图，但在 .venv 下已验证可安全 import）
from agent_graph import (
    MAX_VERIFY,
    _wants_multiple_recipes,
    _message_has_image,
    _drop_orphan_tool_messages,
    _build_structure_context,
    _latest_user_text,
    _messages_for_current_turn,
    maybe_condense,
    verify_answer_node,
)


class TestWantsMultiple(unittest.TestCase):
    """多菜模式判定"""

    def test_single_default(self):
        msgs = [HumanMessage(content="帮我做道番茄炒蛋")]
        self.assertFalse(_wants_multiple_recipes(msgs))

    def test_multiple_keywords(self):
        for w in ["多几道", "几道菜", "供我选择", "多推荐几道"]:
            msgs = [HumanMessage(content=f"帮我{w}")]
            self.assertTrue(_wants_multiple_recipes(msgs), f"关键词「{w}」应开启多菜")


class TestMessageHasImage(unittest.TestCase):
    """图文混合消息识别"""

    def test_text_only(self):
        self.assertFalse(_message_has_image(HumanMessage(content="文字问题")))

    def test_image_mixed(self):
        msg = HumanMessage(content=[
            {"type": "text", "text": "这是什么菜"},
            {"type": "image_url", "image_url": {"url": "http://x/y.jpg"}},
        ])
        self.assertTrue(_message_has_image(msg))


class TestDropOrphanToolMessages(unittest.TestCase):
    """孤儿 ToolMessage（找不到对应 AIMessage tool_calls）应被过滤"""

    def test_orphan_removed(self):
        msgs = [
            HumanMessage(content="hi"),
            # 这条 ToolMessage 的 tool_call_id 在历史里没有对应 AIMessage tool_calls
            ToolMessage(content="orphan result", name="web_search", tool_call_id="no_such_id"),
        ]
        out = _drop_orphan_tool_messages(msgs)
        self.assertEqual(len(out), 1, "孤儿 ToolMessage 应被剔除")
        self.assertIsInstance(out[0], HumanMessage)

    def test_paired_kept(self):
        msgs = [
            AIMessage(content="", tool_calls=[{"id": "c1", "name": "web_search", "args": {}}]),
            ToolMessage(content="ok", name="web_search", tool_call_id="c1"),
        ]
        out = _drop_orphan_tool_messages(msgs)
        self.assertEqual(len(out), 2, "配对完整的 ToolMessage 应保留")


class TestBuildStructureContext(unittest.TestCase):
    """结构化前：从消息里正确抽取搜索图链接与来源标记"""

    def test_real_image_extracted(self):
        msgs = [
            HumanMessage(content="做道番茄炒蛋"),
            ToolMessage(
                content=json.dumps({"text": "菜谱...", "image_url": "http://x/egg.jpg", "image_source": "real"}),
                name="web_search", tool_call_id="t1",
            ),
            AIMessage(content="推荐番茄炒蛋，经典家常菜"),
        ]
        ctx, img, ai = _build_structure_context(msgs)
        self.assertEqual(img, "http://x/egg.jpg")
        self.assertFalse(ai, "real 来源不应标记为 AI 生成")

    def test_ai_image_flagged(self):
        msgs = [
            HumanMessage(content="做道红烧肉"),
            ToolMessage(
                content=json.dumps({"text": "菜谱...", "image_url": "http://x/pic.png", "image_source": "ai"}),
                name="web_search", tool_call_id="t2",
            ),
        ]
        ctx, img, ai = _build_structure_context(msgs)
        self.assertEqual(img, "http://x/pic.png")
        self.assertTrue(ai, "ai 来源应标记为 AI 生成（透明标注依据）")

    def test_only_last_three_searches(self):
        blocks = []
        for i in range(5):
            blocks.append(ToolMessage(
                content=json.dumps({"text": f"r{i}", "image_url": None, "image_source": "real"}),
                name="web_search", tool_call_id=f"t{i}",
            ))
        msgs = [HumanMessage(content="x")] + blocks
        ctx, img, ai = _build_structure_context(msgs)
        # 只取最近 3 次，应出现 r2/r3/r4 而不含 r0/r1
        self.assertIn("r2", ctx)
        self.assertNotIn("r0", ctx)


class TestVerifyAnswerNode(unittest.TestCase):
    """新增 L3 护栏节点：ok / retry / degraded 三态"""

    def _state(self, user_text, ai_text, attempts=0):
        return {
            "messages": [
                HumanMessage(content=user_text),
                AIMessage(content=ai_text),
            ],
            "verify_attempts": attempts,
        }

    def test_ok_when_clean(self):
        st = self._state("我有痛风", "推荐清蒸冬瓜，清淡少油")
        res = verify_answer_node(st)
        self.assertEqual(res["verify_status"], "ok")
        self.assertEqual(res["verify_attempts"], 1)

    def test_retry_when_bad_and_under_limit(self):
        st = self._state("我有痛风和尿酸高", "推荐老火汤炖猪肝配啤酒", attempts=0)
        res = verify_answer_node(st)
        self.assertEqual(res["verify_status"], "retry")
        self.assertEqual(res["verify_attempts"], 1)
        # retry 必须往 state 注入一条反馈消息，驱动 chef_think 重生成
        self.assertIn("messages", res)
        self.assertIsInstance(res["messages"][0], HumanMessage)
        self.assertIn("健康护栏", res["messages"][0].content)

    def test_degraded_when_exhausted(self):
        st = self._state("痛风", "老火汤炖猪肝", attempts=MAX_VERIFY)
        res = verify_answer_node(st)
        self.assertEqual(res["verify_status"], "degraded")
        self.assertTrue(res["verify_warning"].startswith("⚠️"),
                        "超限仍不通过应带安全警示，绝不静默放行")

    def test_uses_latest_ai_text(self):
        # 历史里有一条合规 AI 回答，但最新一条违规 -> 应判 retry
        st = {
            "messages": [
                HumanMessage(content="痛风"),
                AIMessage(content="清蒸冬瓜"),          # 旧：合规
                AIMessage(content="老火汤炖猪肝"),       # 新：违规
            ],
            "verify_attempts": 0,
        }
        res = verify_answer_node(st)
        self.assertEqual(res["verify_status"], "retry")


class TestHistoryIsolation(unittest.TestCase):
    def test_new_image_excludes_old_recipe_and_tool_result(self):
        new_image = HumanMessage(content=[
            {"type": "text", "text": "这些新食材能做什么"},
            {"type": "image_url", "image_url": {"url": "http://x/new.jpg"}},
        ])
        msgs = [
            HumanMessage(content="做红烧肉"),
            AIMessage(content="红烧肉方案"),
            ToolMessage(content="old search", name="web_search", tool_call_id="old"),
            new_image,
            AIMessage(content="识别到番茄和鸡蛋"),
        ]
        current = _messages_for_current_turn(msgs, isolate_old_context=True)
        self.assertIs(current[0], new_image)
        self.assertNotIn("红烧肉方案", [str(m.content) for m in current])
        self.assertNotIn("old search", [str(m.content) for m in current])

    def test_condense_removes_tool_result_with_deleted_call(self):
        msgs = [
            HumanMessage(content="旧问题", id="h-old"),
            AIMessage(content="", tool_calls=[{"id": "call-old", "name": "web_search", "args": {}}], id="a-old"),
            ToolMessage(content="old result", name="web_search", tool_call_id="call-old", id="t-old"),
        ]
        for i in range(3):
            msgs.extend([
                AIMessage(content=f"回答{i}", id=f"a{i}"),
                HumanMessage(content=f"问题{i}", id=f"h{i}"),
            ])
        with patch("agent_graph.summary_llm") as mock_llm:
            mock_llm.invoke.return_value = AIMessage(content="摘要")
            result = maybe_condense({"messages": msgs})
        removed_ids = {m.id for m in result["messages"] if m.__class__.__name__ == "RemoveMessage"}
        self.assertIn("a-old", removed_ids)
        self.assertIn("t-old", removed_ids)


class TestVerifyBoundary(unittest.TestCase):
    def test_last_allowed_retry_before_degraded(self):
        state = {
            "messages": [HumanMessage(content="我有痛风"), AIMessage(content="老火汤炖猪肝")],
            "verify_attempts": MAX_VERIFY - 1,
        }
        result = verify_answer_node(state)
        self.assertEqual(result["verify_status"], "retry")
        self.assertEqual(result["verify_attempts"], MAX_VERIFY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
