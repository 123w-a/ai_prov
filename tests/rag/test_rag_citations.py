"""RAG 来源展示的确定性测试：chunk 序号不能伪装成 PDF 页码。"""

import unittest

from agent_tools import _legacy

_resolve_section = _legacy._resolve_section


class ResolveSectionTest(unittest.TestCase):
    def test_legacy_chunk_anchor_is_not_treated_as_page(self):
        self.assertEqual(
            _resolve_section("guide.pdf", "guide.pdf_p7"),
            "guide.pdf_p7",
        )

    def test_chunk_anchor_is_not_rendered_as_fragment(self):
        self.assertEqual(
            _resolve_section("guide.pdf", "guide.pdf_chunk41"),
            "guide.pdf_chunk41",
        )

    def test_compact_chunk_anchor_is_not_treated_as_page(self):
        self.assertEqual(
            _resolve_section("guide.pdf", "guide.pdf_c41"),
            "guide.pdf_c41",
        )

    def test_real_page_anchor_is_preserved(self):
        self.assertEqual(
            _resolve_section("guide.pdf", "guide.pdf_p28"),
            "guide.pdf_p28",
        )

    def test_heading_anchor_is_preserved(self):
        self.assertEqual(
            _resolve_section("guide.md", "第二章 膳食原则"),
            "第二章 膳食原则",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
