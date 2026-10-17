"""Agent 轨迹观测的确定性回归测试（真实 trace 文件一律打 patch 隔离）。"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import trace as agent_trace


class AgentTraceTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(".agent-trace-test.jsonl")
        self.patcher = patch.object(agent_trace, "_FILE", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.path.unlink(missing_ok=True)

    def test_decorator_records_node_cost_and_passes_result(self):
        @agent_trace.trace_node("chef_think")
        def node(state):
            return {"messages": ["ok"]}

        state = {"messages": ["a" * 3, "b" * 5]}

        self.assertEqual(node(state), {"messages": ["ok"]})

        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["node"], "chef_think")
        self.assertGreaterEqual(record["ms"], 0)
        self.assertEqual(record["msgs"], 2)
        self.assertEqual(record["chars"], 8)
        self.assertIn("ts", record)

    def test_append_failure_is_silent_for_caller(self):
        blocker = Path(__file__).with_name(".agent-trace-block")
        target = blocker / "x.jsonl"

        try:
            blocker.write_text("占据同名路径，使目录创建必然失败", encoding="utf-8")
            with patch.object(agent_trace, "_FILE", target):
                @agent_trace.trace_node("ask_user")
                def node(_state):
                    return "unchanged"

                self.assertEqual(node({}), "unchanged")
        finally:
            blocker.unlink(missing_ok=True)

        self.assertFalse(target.exists())

    def test_state_both_dict_and_object_shapes_supported(self):
        class ObjState:
            messages = [type("M", (), {"content": "你好"})()]

        @agent_trace.trace_node("verify_answer")
        def node(state):
            return None

        with patch.object(agent_trace, "_append") as spy:
            node({"messages": []})
            node(ObjState())
            calls = [c.args[0] for c in spy.call_args_list]

        self.assertEqual([c["msgs"] for c in calls], [0, 1])
        self.assertEqual(calls[1]["chars"], 2)

    def test_records_session_and_turn_seq_for_per_turn_attribution(self):
        """按轮归因的唯一锚点：没有这两个字段就只能靠时间间隔猜轮次边界。"""

        @agent_trace.trace_node("chef_think")
        def node(state):
            return {"messages": []}

        node({"messages": [], "session_id": "user_1", "turn_seq": 7})

        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["session_id"], "user_1")
        self.assertEqual(record["turn_seq"], 7)

    def test_missing_session_and_turn_seq_fall_back_to_empty_and_zero(self):
        @agent_trace.trace_node("chef_think")
        def node(state):
            return {"messages": []}

        node({"messages": []})

        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["session_id"], "")
        self.assertEqual(record["turn_seq"], 0)

    def test_turn_seq_prefers_value_produced_by_the_node(self):
        """入口节点在本轮把 turn_seq 自增，记录必须落在这一轮而不是上一轮。"""

        @agent_trace.trace_node("condense_history")
        def node(state):
            return {"turn_seq": 8, "messages": []}

        node({"messages": [], "turn_seq": 7})

        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["turn_seq"], 8)

    def test_diag_records_guardrail_fields_without_model_text(self):
        """护栏判定要落盘（用来定位谁把这一轮打回的），但正文绝不进观测文件。"""

        @agent_trace.trace_node("verify_answer")
        def node(state):
            return {
                "verify_status": "retry",
                "verify_attempts": 2,
                "tool_rounds": 3,
                "tool_budget_exhausted": False,
                "verify_violated": ["过敏原:花生及其制品"],
                "messages": ["模型正文不该进 diag"],
            }

        node({"messages": []})

        diag = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])["diag"]
        self.assertEqual(diag["verify_status"], "retry")
        self.assertEqual(diag["verify_attempts"], 2)
        self.assertEqual(diag["tool_rounds"], 3)
        self.assertFalse(diag["tool_budget_exhausted"])
        self.assertEqual(diag["verify_violated"], ["过敏原:花生及其制品"])
        self.assertNotIn("messages", diag)

    def test_diag_absent_when_node_returns_non_dict(self):
        @agent_trace.trace_node("ask_user")
        def node(_state):
            return "unchanged"

        node({"messages": []})

        record = json.loads(self.path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("diag", record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
