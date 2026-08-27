"""Agent 轨迹观测的确定性回归测试（真实 trace 文件一律打 patch 隔离）。"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_trace


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
