"""切片策略路由与 parent-child 完整单元的确定性回归。"""

import unittest

from rag.chunking import chunk_by_parent_child
from rag.chunking_router import STRATEGIES, choose_chunker, probe_features


class ChunkingRouterTest(unittest.TestCase):
    def test_all_strategy_slots_are_preserved(self):
        self.assertEqual(
            STRATEGIES,
            (
                "heading_parent",
                "paragraph",
                "parent_child",
                "semantic",
                "proposition",
            ),
        )

    def test_markdown_with_headings_uses_heading_parent(self):
        strategy, reason = choose_chunker(
            "## 膳食原则\n正文",
            {"content_type": "markdown", "category": "0_总纲"},
        )
        self.assertEqual(strategy, "heading_parent")
        self.assertIn("Markdown", reason)

    def test_chronic_guide_uses_parent_child(self):
        strategy, reason = choose_chunker(
            "一、食养原则\n1. 减钠增钾\n2. 合理膳食",
            {"content_type": "pdf", "category": "1_慢病食养指南"},
        )
        self.assertEqual(strategy, "parent_child")
        self.assertIn("完整知识单元", reason)

    def test_plain_document_uses_paragraph(self):
        strategy, _ = choose_chunker(
            "普通说明文字。" * 20,
            {"content_type": "pdf", "category": "2_营养素参考摄入量DRI"},
        )
        self.assertEqual(strategy, "paragraph")

    def test_probe_is_deterministic_and_counts_structures(self):
        features = probe_features(
            "## 标题\n1. 条目一\n2. 条目二\nA    B    C",
            {"content_type": "markdown"},
        )
        self.assertTrue(features["has_headings"])
        self.assertGreaterEqual(features["item_count"], 2)
        self.assertGreaterEqual(features["table_count"], 1)

    def test_parent_is_kept_and_each_child_retains_page_marker(self):
        text = (
            "<<<PAGE:2>>>\n"
            "1. 第一条完整规则，包含需要完整保留的说明。\n"
            "继续说明。\n\n"
            "<<<PAGE:3>>>\n"
            "2. 第二条规则。"
        )
        chunks = chunk_by_parent_child(text, max_chars=1200, overlap_chars=120)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].child_text, chunks[0].parent_text)
        self.assertIn("<<<PAGE:2>>>", chunks[0].child_text)
        self.assertIn("<<<PAGE:3>>>", chunks[1].child_text)
        self.assertTrue(all(item.atomic_parent for item in chunks))


if __name__ == "__main__":
    unittest.main(verbosity=2)
