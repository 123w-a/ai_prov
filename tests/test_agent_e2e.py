"""Agent 全链路端到端冒烟测试（真实 LLM + 真实工具，会消耗 API 额度）。

默认跳过；需设环境变量 RUN_E2E=1 才运行（避免每次跑测试都烧额度）：
    RUN_E2E=1 .venv\\Scripts\\python.exe tests\\run_all.py

验证点：
1) 整图在真实请求下能从 entry 跑到 END，不卡死、不抛未捕获异常；
2) 健康护栏节点 verify_answer 确实在真实链路中被执行（不只是单元测试里）；
3) 最终产出结构化卡片，必填字段齐全、recipes 非空；
4) 健康/慢病上下文确实流入卡片（护栏 + RAG 生效的间接证据）。
"""
import os, sys, json, time, unittest

sys.path.insert(0, r"D:\ai_prvo")

from langchain_core.messages import HumanMessage

RUN_E2E = os.environ.get("RUN_E2E") == "1"


@unittest.skipUnless(RUN_E2E, "设 RUN_E2E=1 才跑真实 LLM 链路（耗 API 额度/较慢，约 1-3 分钟）")
class TestAgentE2E(unittest.TestCase):
    def _run(self, text):
        import agent_graph as ag  # 延迟导入：仅在真正跑 e2e 时初始化 LLM + 编译图
        tid = "e2e-%d-%d" % (int(time.time() * 1000), os.getpid())
        cfg = {"configurable": {"thread_id": tid}}
        events = []
        for chunk in ag.agent.stream(
            {"messages": [HumanMessage(content=text)]},
            config=cfg,
            stream_mode="updates",
        ):
            events.append(chunk)
        state = ag.agent.get_state(cfg)
        return events, state

    def test_health_sensitive_query_runs_full_chain(self):
        events, state = self._run(
            "我有高血压，用芹菜和木耳做一道清淡的菜，只要一道，少盐"
        )

        # 1) 健康护栏节点在真实链路中确实执行了
        va_chunks = [c for c in events if "verify_answer" in c]
        self.assertTrue(va_chunks, "verify_answer 护栏节点未在真实运行中执行")
        status = va_chunks[-1]["verify_answer"].get("verify_status")
        self.assertIn(status, ("ok", "retry", "degraded"),
                      f"verify_status 非法: {status}")

        # 2) 最终产出结构化卡片（解析为 JSON，必填字段齐全）
        msgs = state.values["messages"]
        last = msgs[-1]
        self.assertEqual(type(last).__name__, "AIMessage", "末条消息应为结构化 AIMessage")
        obj = json.loads(last.content)
        for k in ("opening", "recipes", "chef_tip", "sources"):
            self.assertIn(k, obj, f"结构化卡片缺少字段 {k}")
        self.assertTrue(len(obj["recipes"]) >= 1, "recipes 不应为空")

        # 3) 健康/RAG 上下文确实流入（护栏或检索生效的间接证据）
        flowed = bool(obj.get("sources")) or ("健康" in obj.get("chef_tip", ""))
        self.assertTrue(flowed, "未观察到健康护栏/RAG 上下文流入卡片")


if __name__ == "__main__":
    unittest.main(verbosity=2)
