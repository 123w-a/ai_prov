# tests/test_graph_smoke.py
# 冒烟测试：确认 LangGraph 整图能编译、关键节点/边齐全（导入即触发 compile）
import unittest

import agent_graph


class TestGraphCompiles(unittest.TestCase):
    """整图编译冒烟"""

    def test_agent_object_exists(self):
        self.assertTrue(hasattr(agent_graph, "agent"), "agent 图实例应已编译生成")

    def test_verify_node_registered(self):
        # 新加的 verify_answer 节点必须在图中
        nodes = set(agent_graph.agent.get_graph().nodes.keys())
        for n in ["chef_think", "run_tools", "verify_answer", "structure_answer", "condense_history"]:
            self.assertIn(n, nodes, f"缺失节点 {n}")

    def test_state_has_guard_fields(self):
        from agent_graph import ChefState
        self.assertIn("verify_attempts", ChefState.__annotations__)
        self.assertIn("verify_warning", ChefState.__annotations__)
        self.assertIn("verify_status", ChefState.__annotations__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
