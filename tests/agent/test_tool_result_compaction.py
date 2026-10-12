# tests/agent/test_tool_result_compaction.py
# 工具结果压缩（「三旋钮」的第三只：结果体积）——全部确定性，不调 LLM / 不联网。
#
# 背景：省 token 的真杠杆在结果体积，不在调用次数。一次 tool_call 的 JSON 只有
# 几十~一两百 token，但一条工具结果动辄 3–10KB ≈ 1k–3k token，且随每轮历史重复投喂。
# 这里断言四件事：① 结构不变（下游按 key 取值）；② 冗余字段被去掉；③ 超长被截断且标注；
# ④ 解析失败/非 JSON 时退化为整体截断，绝不丢内容形态。
import json
import unittest

from langchain_core.messages import ToolMessage

import agent.graph as g


def _tool_msg(name, payload):
    return ToolMessage(content=payload if isinstance(payload, str) else
                       json.dumps(payload, ensure_ascii=False), tool_call_id="t1", name=name)


class ToolResultCompactionTest(unittest.TestCase):

    def test_web_search_long_text_is_clipped_with_marker(self):
        payload = {"text": "做法" * 5000, "image_url": "http://x/y.png", "image_source": "real"}
        out = json.loads(g._compact_tool_result(_tool_msg("web_search", payload)))
        self.assertLessEqual(len(out["text"]), g._TOOL_RESULT_LIMITS["web_search"] + 8)
        self.assertTrue(out["text"].endswith("…（已截断）"))
        # 短字段必须原样保留：结构化链要用 image_url / image_source 判定透明标注
        self.assertEqual(out["image_url"], "http://x/y.png")
        self.assertEqual(out["image_source"], "real")

    def test_short_result_is_untouched(self):
        payload = {"text": "很短的结果", "image_url": None, "image_source": "none"}
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(g._compact_tool_result(_tool_msg("web_search", payload)), raw)

    def test_kb_hits_drop_redundant_excerpt_when_text_exists(self):
        hits = [{
            "source": f"指南{i}.pdf", "section": "少盐", "distance": 0.1,
            "excerpt": "与 text 重复的片段" * 40,
            "text": "正文" * 600,
            "metadata": {"anchor": f"指南{i}.pdf_p{10 + i}"},
        } for i in range(10)]
        out = json.loads(g._compact_tool_result(
            _tool_msg("nutrition_kb_search", {"found": True, "hits": hits})))
        self.assertEqual(len(out["hits"]), g._MAX_RESULT_ITEMS)
        for hit in out["hits"]:
            # excerpt 与 text 高度重复，压掉；source/anchor 是引用依据，必须留着
            self.assertNotIn("excerpt", hit)
            self.assertIn("source", hit)
            self.assertIn("anchor", hit["metadata"])
            self.assertLessEqual(len(hit["text"]), 800 + 8)
        self.assertTrue(out["found"])

    def test_kb_hits_keep_excerpt_when_no_text(self):
        # 真实 nutrition_kb_search 只返回 excerpt（见 agent_tools/__init__.py），
        # 没有 text。此时若仍按「excerpt 冗余」丢掉，命中片段会在进结构化链前
        # 整个消失，sources.snippet 无原文可取——只能靠模型编或留空。
        hits = [{
            "source": f"指南{i}.pdf", "section": "少盐", "distance": 0.1,
            "excerpt": "减盐片段" * 300,
            "metadata": {"anchor": f"指南{i}.pdf_p{10 + i}"},
        } for i in range(10)]
        out = json.loads(g._compact_tool_result(
            _tool_msg("nutrition_kb_search", {"found": True, "hits": hits})))
        self.assertEqual(len(out["hits"]), g._MAX_RESULT_ITEMS)
        for hit in out["hits"]:
            # 没有 text 时 excerpt 是唯一正文来源，必须保留并截断
            self.assertNotIn("text", hit)
            self.assertIn("excerpt", hit)
            self.assertTrue(hit["excerpt"].startswith("减盐片段"))
            self.assertLessEqual(len(hit["excerpt"]), 800 + 8)
            self.assertTrue(hit["excerpt"].endswith("…（已截断）"))
            self.assertIn("source", hit)
            self.assertIn("anchor", hit["metadata"])
        self.assertTrue(out["found"])

    def test_non_json_falls_back_to_whole_clip(self):
        out = g._compact_tool_result(_tool_msg("web_search", "纯文本" * 5000))
        self.assertTrue(out.endswith("…（已截断）"))
        self.assertLessEqual(len(out), g._TOOL_RESULT_LIMITS["web_search"] + 8)

    def test_json_array_result_is_clipped(self):
        out = g._compact_tool_result(_tool_msg("web_search", "[1,2,3]" + "x" * 5000))
        self.assertLessEqual(len(out), g._TOOL_RESULT_LIMITS["web_search"] + 8)

    def test_unknown_tool_uses_default_limit(self):
        payload = {"text": "内容" * 3000}
        out = json.loads(g._compact_tool_result(_tool_msg("some_new_tool", payload)))
        self.assertLessEqual(len(out["text"]), g._MAX_RESULT_CHARS_DEFAULT + 8)

    def test_budget_report_counts_saved_chars(self):
        messages = [
            _tool_msg("web_search", {"text": "做法" * 5000, "image_url": None}),
            _tool_msg("nutrition_kb_search", {"found": True, "hits": [
                {"source": "a.pdf", "excerpt": "e" * 2000, "text": "t" * 3000}]}),
        ]
        report = g._tool_result_budget_report(messages)
        self.assertEqual(report["tools"], 2)
        self.assertGreater(report["saved"], 0)
        self.assertLess(report["after"], report["before"])
        self.assertGreater(report["saved_pct"], 0)

    def test_evidence_uses_compacted_result(self):
        # 收口证据走同一套压缩：既控长度，也不把 JSON 再塞回上下文
        messages = [
            _tool_msg("nutrition_kb_search", {"found": True, "hits": [
                {"source": "指南.pdf", "excerpt": "x" * 500, "text": "正文" * 400}]}),
        ]
        evidence = g._tool_result_evidence(messages)
        self.assertIn("指南.pdf", evidence)
        self.assertIn("正文", evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
