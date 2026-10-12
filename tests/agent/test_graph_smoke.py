# tests/agent/test_graph_smoke.py
# 冒烟测试：确认 LangGraph 整图能编译、关键节点/边齐全（导入即触发 compile）
import unittest

import agent.graph as agent_graph


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
        from agent.graph import ChefState
        self.assertIn("verify_attempts", ChefState.__annotations__)
        self.assertIn("verify_warning", ChefState.__annotations__)
        self.assertIn("verify_status", ChefState.__annotations__)


class TestNodeInputSchema(unittest.TestCase):
    """防回归：节点首参注解若写成 MessagesState，LangGraph 会按它裁掉投给该节点的 state。

    LangGraph 用「节点函数第一个参数的类型注解」推断 input_schema，运行时只往该 schema
    包含的通道投喂 state。注解成 MessagesState 时，节点里所有
    `state.get("tool_budget" / "pick_turn_slot_granted" / "ingredient_gate_ready" ...)`
    全部读到 None —— 而现有单测都是 `g.chef_agent_node({...})` 直传裸 dict，绕过图裁剪，
    所以看不到这个缺陷。

    实测（2026-10-12）：`chef_agent_node(state: MessagesState)` 让 chef_think 只收到
    `{'messages'}`，`_pick_turn_slot_patch` 里 `_remaining_tool_slots(state)` 退回默认值
    4 → 补额度分支恒不命中；选定轮第二圈被 `chef_route_with_tool_budget` 判预算耗尽，
    用户已经看到的正文被 `tool_budget_finalize` 的兜底句顶掉。只把注解改成 "ChefState"，
    全链路复现即恢复正常。这里把「读扩展通道的节点必须拿到完整 state」钉成断言。
    """

    FULL_STATE_NODES = [
        "chef_think",
        "run_tools",
        "verify_answer",
        "tool_budget_finalize",
        "allergen_block",
        "profile_memory",
        "profile_gate",
        "ask_user",
        "ingredient_gate",
        "ingredient_confirm",
        "requested_dish_gate",
        "structure_answer",
    ]

    CHANNELS_READ_BY_CHEF_THINK = [
        "session_id",
        "tool_budget",
        "tool_calls_in_turn",
        "tool_rounds",
        "pick_turn_slot_granted",
        "ingredient_gate_ready",
        "ingredient_draft",
    ]

    CHANNELS_READ_BY_STRUCTURE_ANSWER = [
        "session_id",
        "ingredient_gate_ready",
        "verify_violated",
        "verify_status",
        "verify_warning",
    ]

    def test_state_reading_nodes_receive_full_state(self):
        from agent.graph import ChefState

        for name in self.FULL_STATE_NODES:
            with self.subTest(node=name):
                spec = agent_graph.workflow.nodes[name]
                self.assertIs(
                    spec.input_schema,
                    ChefState,
                    f"节点 {name} 的 input_schema 被裁成 {{'messages'}}，"
                    "节点内所有扩展通道都会读成 None（检查首参注解）",
                )

    def test_chef_think_sees_its_extended_channels(self):
        spec = agent_graph.workflow.nodes["chef_think"]
        channels = set(getattr(spec.input_schema, "__annotations__", {}))
        for channel in self.CHANNELS_READ_BY_CHEF_THINK:
            with self.subTest(channel=channel):
                self.assertIn(
                    channel,
                    channels,
                    f"chef_think 收不到 {channel}，该通道上的既有逻辑会静默失效",
                )

    def test_structure_answer_sees_its_extended_channels(self):
        """structure_answer 同样读扩展通道：注解做成 MessagesState 时，
        下面的逻辑会静默失效（实测 2026-10-12 修复）：
        - `ingredient_gate_ready` 恒 False → 候选清单「先交代推荐依据」变体成死代码；
        - `session_id` 恒 None → `_allergens_for_audit` 拿不到会话级过敏原，
          结构化卡片复核漏掉用户已确认的忌口；
        - `verify_violated/verify_status/verify_warning` 恒空 → 正文层命中的
          病种不再并入卡片护栏，`_build_guardrails` 全走 pass 分支，
          过敏原打回后的安全替代说明也不出现。
        """
        spec = agent_graph.workflow.nodes["structure_answer"]
        channels = set(getattr(spec.input_schema, "__annotations__", {}))
        for channel in self.CHANNELS_READ_BY_STRUCTURE_ANSWER:
            with self.subTest(channel=channel):
                self.assertIn(
                    channel,
                    channels,
                    f"structure_answer 收不到 {channel}，该通道上的既有逻辑会静默失效",
                )


class StructureAnswerCompiledGraphStateTest(unittest.TestCase):
    """整图回归：structure_answer 必须在**真实编译图**里拿到扩展通道。

    2026-10-12 根因同 TestNodeInputSchema：首参注解写成 `MessagesState` 时，
    编译图只投 `{'messages'}`，节点内 `state.get("verify_status" / "verify_violated" /
    "verify_warning" / "session_id")` 全读成默认值，`_build_guardrails` 恒走 pass 分支、
    会话级过敏原复核失效。既有单测都直传裸 dict，绕过图裁剪，测不到这个缺陷。
    """

    def test_node_receives_verify_and_session_channels(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.graph import END, START, StateGraph
        from langgraph.graph.message import add_messages

        from agent.graph import ChefState
        from agent.schemas import ChefAnswer, Recipe, Seasoning
        from agent import graph as g
        from agent import trace as trace_mod

        captured = {}

        def _fake_structured(context):
            return ChefAnswer(recipes=[Recipe(
                name="番茄炒蛋",
                intro="少油少盐版",
                difficulty=1,
                nutrition=4,
                seasonings=[Seasoning(name="盐", amount="少许")],
                steps=["番茄切块", "少油翻炒"],
            )])

        def _capture_guardrails(latest_text, verify_status, verify_violated, **kwargs):
            captured["verify_status"] = verify_status
            captured["verify_violated"] = list(verify_violated)
            captured["session_id"] = kwargs.get("session_id")
            return []

        def _capture_allergens(session_id, *args, **kwargs):
            captured.setdefault("audit_session_ids", []).append(session_id)
            return set()

        builder = StateGraph(ChefState)
        builder.add_node("structure_answer", g.structure_answer_node)
        builder.add_edge(START, "structure_answer")
        builder.add_edge("structure_answer", END)
        app = builder.compile()

        # 选定轮形态：先给候选清单，再给「已选定候选」锚点，正文是单道菜。
        candidates = {
            "opening": "挑一道",
            "recipes": [],
            "candidates": [{"name": "番茄炒蛋"}, {"name": "番茄蛋汤"}],
            "image_url": None,
            "image_ai_generated": False,
            "image_requested": False,
            "image_note": "",
            "chef_tip": "",
            "sources": [],
            "health_lights": [],
            "guardrails": [],
        }
        state_in = {
            "messages": add_messages([], [
                HumanMessage(content="我有番茄和鸡蛋"),
                AIMessage(content=json.dumps(candidates, ensure_ascii=False)),
                HumanMessage(content=(
                    "【已选定候选：番茄炒蛋】\n"
                    "（用户用序号选定了上一轮候选清单里的这一道，本轮必须围绕它展开。）\n\n"
                    "番茄炒蛋，少油少盐"
                )),
            ]),
            "session_id": "structure-answer-graph-test",
            "verify_status": "degraded",
            "verify_violated": ["高血压:高盐"],
            "verify_warning": "⚠️ 健康护栏提示：多次重生成仍含需注意项",
            "ingredient_gate_ready": False,
        }
        trace_path = Path(tempfile.gettempdir()) / "probe_structure_answer_graph_test.jsonl"
        final = None
        with patch.object(g, "build_structured_answer", _fake_structured), \
                patch.object(g, "rank_recipes", lambda answer, **kwargs: answer), \
                patch.object(g, "_search_recipe_image", lambda name: (None, False)), \
                patch.object(g, "_build_guardrails", _capture_guardrails), \
                patch.object(g, "_allergens_for_audit", _capture_allergens), \
                patch.object(trace_mod, "_FILE", trace_path):
            for chunk in app.stream(
                state_in,
                {"configurable": {"thread_id": "structure-answer-graph-test"}},
                stream_mode="values",
            ):
                final = chunk

        payload_text = str(getattr((final.get("messages") or [None])[-1], "content", ""))
        self.assertNotIn(
            '"recipes": []',
            payload_text,
            "本用例应走到单菜结构化，却被提前短路（消息形态或图裁剪发生了变化）",
        )
        self.assertIn(
            "⚠️ 健康护栏提示",
            payload_text,
            "verify_warning 没进卡片 chef_tip —— structure_answer 读不到该通道",
        )

        self.assertEqual(
            captured.get("session_id"),
            "structure-answer-graph-test",
            "structure_answer 读不到 session_id —— 结构化过敏原复核会漏掉会话级忌口",
        )
        self.assertIn(
            "structure-answer-graph-test",
            captured.get("audit_session_ids", []),
            "structure_answer 读不到 session_id —— _allergens_for_audit 拿到的不是真实会话",
        )
        self.assertEqual(
            captured.get("verify_status"),
            "degraded",
            "structure_answer 读不到 verify_status —— 护栏全走 pass 分支",
        )
        self.assertEqual(
            captured.get("verify_violated"),
            ["高血压:高盐"],
            "structure_answer 读不到 verify_violated —— 正文层命中的病种没并进卡片护栏",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
