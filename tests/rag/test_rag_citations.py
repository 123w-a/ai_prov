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

    def test_physical_page_is_calibrated_to_printed_page(self):
        # 《成人高血压食养指南》印刷页 = 物理页 - 2；索引锚点记录物理页 6。
        source = "成人高血压食养指南（2023年版）.pdf"
        self.assertEqual(
            _resolve_section(source, f"{source}_p6"),
            f"{source}_p4",
        )

    def test_offset_one_document_is_calibrated(self):
        source = "WS_T_578.1_宏量营养素.pdf"
        self.assertEqual(
            _resolve_section(source, f"{source}_p3"),
            f"{source}_p2",
        )

    def test_page_before_first_printed_page_keeps_physical(self):
        # 封面换算后小于第 1 页，退回物理页码，不显示「第 0 页」。
        source = "成人高血压食养指南（2023年版）.pdf"
        self.assertEqual(
            _resolve_section(source, f"{source}_p2"),
            f"{source}_p2",
        )

    def test_unmapped_document_keeps_physical_page(self):
        source = "福建省中小学生营养食谱（试行）.pdf"
        self.assertEqual(
            _resolve_section(source, f"{source}_p8"),
            f"{source}_p8",
        )

    def test_chunk_anchor_of_mapped_document_is_not_calibrated(self):
        source = "成人高血压食养指南（2023年版）.pdf"
        self.assertEqual(
            _resolve_section(source, f"{source}_c41"),
            f"{source}_c41",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
