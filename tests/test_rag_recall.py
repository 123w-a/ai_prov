"""RAG 召回质量测试（纯本地检索，不调 LLM，确定性、毫秒级）。

验证 nutrition_rules 护杠的「知识来源」——权威健康知识库能否被准确召回、可溯源。
这是护杠端到端生效的前提：verify_answer 审计时依赖 nutrition_kb_search 召回的准确规则。
"""
import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rag.retriever import search as kb_search          # 底层检索
from agent_tools import nutrition_kb_search            # 工具层封装（与 agent 实际调用一致）


class TestRagRecall(unittest.TestCase):
    def test_chronic_disease_query_returns_authoritative_doc(self):
        """慢病查询应召回权威指南/食养文档（护杠可溯源的前提）。"""
        r = kb_search("高血压 低盐 膳食 原则 怎么吃", n_results=3)
        self.assertFalse(r.error, msg=f"检索报错: {r.error}")
        self.assertTrue(len(r.hits) > 0, "高血压查询无召回结果")
        sources = [h.source for h in r.hits]
        self.assertTrue(
            any(("指南" in s or "食养" in s) for s in sources),
            f"未召回权威指南/食养文档，实际来源: {sources}",
        )

    def test_gout_forbidden_food_query_recalls_gout_guide(self):
        """痛风/高尿酸忌口查询应召回对应食养指南，且命中带 source+section（可溯源）。"""
        r = kb_search("痛风 忌口 高尿酸 黄豆 猪肝 老火汤", n_results=3)
        self.assertFalse(r.error, msg=f"检索报错: {r.error}")
        self.assertTrue(len(r.hits) > 0, "痛风忌口查询无召回")
        for h in r.hits:
            self.assertTrue(h.source, "命中缺少 source（不可溯源）")
            self.assertIsNotNone(h.section, "命中缺少 section")

    def test_tool_nutrition_kb_search_json_shape(self):
        """工具层 nutrition_kb_search 返回结构应符合 agent 解析约定。

        nutrition_kb_search 在包里是 StructuredTool（与 Agent 运行时 ToolNode 调用方式一致），
        故用 .invoke(dict) 调用，而非直接当函数调用。
        """
        fn = nutrition_kb_search
        if hasattr(fn, "invoke"):
            raw = fn.invoke({"query": "糖尿病 控糖 主食 怎么吃", "top_k": 3})
        else:
            raw = fn("糖尿病 控糖 主食 怎么吃", top_k=3)
        out = json.loads(raw)
        self.assertTrue(out["found"], "工具层应 found=True")
        self.assertTrue(len(out["hits"]) > 0, "工具层应返回命中")
        hit = out["hits"][0]
        for k in ("source", "section", "distance", "excerpt", "metadata"):
            self.assertIn(k, hit, f"命中缺少字段 {k}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
