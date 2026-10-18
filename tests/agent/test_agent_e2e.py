"""Agent 全链路端到端冒烟测试（真实 LLM + 真实工具，会消耗 API 额度）。

默认跳过；需设环境变量 RUN_E2E=1 才运行（避免每次跑测试都烧额度）：
    RUN_E2E=1 .venv\\Scripts\\python.exe tests\\run_all.py

验证点：
1) 整图在真实请求下能从 entry 跑到 END，不卡死、不抛未捕获异常；
2) 健康护栏节点 verify_answer 确实在真实链路中被执行（不只是单元测试里）；
3) 首轮只出候选清单，选定序号后产出单菜结构化卡片；
4) 健康/慢病上下文确实流入卡片（护栏 + RAG 生效的间接证据）。
"""
import os, sys, json, time, unittest

# 仓库根 = 本文件上溯三层（tests/agent/xxx.py → tests/agent → tests → 仓库根）。
# 2026-10-15 修正：原先这里写死的是 `r"D:\ai_prvo"`——一个既拼错（少了 v）又只
# 在本机成立、且与真实仓库路径不符的绝对路径。协作者 clone 后该目录不存在，
# 于是 sys.path 上挂了个空目录，`import agent.graph` 直接 ImportError，
# 这个 e2e 用例在任何别人的机器上都跑不起来。改为从 __file__ 相对定位。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langchain_core.messages import HumanMessage

RUN_E2E = os.environ.get("RUN_E2E") == "1"


@unittest.skipUnless(RUN_E2E, "设 RUN_E2E=1 才跑真实 LLM 链路（耗 API 额度/较慢，约 1-3 分钟）")
class TestAgentE2E(unittest.TestCase):
    def _run(self, text, thread_id=None):
        import agent.graph as ag  # 延迟导入：仅在真正跑 e2e 时初始化 LLM + 编译图
        tid = thread_id or "e2e-%d-%d" % (int(time.time() * 1000), os.getpid())
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

    @staticmethod
    def _json_payloads(messages):
        """按从新到旧返回消息中的结构化 payload。"""
        payloads = []
        for msg in reversed(messages):
            if type(msg).__name__ != "AIMessage":
                continue
            try:
                payload = json.loads(msg.content)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads

    def test_health_sensitive_query_runs_full_chain(self):
        tid = "e2e-%d-%d" % (int(time.time() * 1000), os.getpid())
        first_events, first_state = self._run(
            "我有高血压，医生让严格控盐，用芹菜和木耳推荐三道清淡菜让我选，都少盐",
            thread_id=tid,
        )

        # 1) 首轮健康护栏执行，并停在候选清单，不提前出菜谱卡
        first_va = [c for c in first_events if "verify_answer" in c]
        self.assertTrue(first_va, "首轮 verify_answer 护栏节点未执行")
        status = first_va[-1]["verify_answer"].get("verify_status")
        self.assertIn(
            status, ("ok", "retry", "degraded"), f"verify_status 非法: {status}"
        )
        candidates = next(
            (
                payload
                for payload in self._json_payloads(first_state.values["messages"])
                if payload.get("answer_kind") == "candidates"
            ),
            None,
        )
        self.assertIsNotNone(candidates, "首轮没有结构化候选清单")
        self.assertGreaterEqual(len(candidates.get("candidates") or []), 2)
        self.assertEqual(candidates.get("recipes"), [], "候选轮不应提前产出菜谱")

        # 2) 同一会话回复序号，进入单菜结构化卡片
        second_events, second_state = self._run("1", thread_id=tid)
        second_va = [c for c in second_events if "verify_answer" in c]
        self.assertTrue(second_va, "选定轮 verify_answer 护栏节点未执行")
        status = second_va[-1]["verify_answer"].get("verify_status")
        self.assertIn(
            status, ("ok", "retry", "degraded"), f"verify_status 非法: {status}"
        )
        card = next(
            (
                payload
                for payload in self._json_payloads(second_state.values["messages"])
                if payload.get("answer_kind") in (None, "recipe")
                and payload.get("recipes")
            ),
            None,
        )
        self.assertIsNotNone(card, "选定后没有产出单菜结构化卡片")
        for key in ("opening", "recipes", "chef_tip", "sources"):
            self.assertIn(key, card, f"结构化卡片缺少字段 {key}")
        self.assertGreaterEqual(len(card["recipes"]), 1, "recipes 不应为空")

        # 3) 健康/RAG 上下文确实流入（护栏或检索生效的间接证据）
        flowed = bool(card.get("sources")) or ("健康" in card.get("chef_tip", ""))
        self.assertTrue(flowed, "未观察到健康护栏/RAG 上下文流入卡片")


if __name__ == "__main__":
    unittest.main(verbosity=2)

