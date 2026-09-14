"""PDF 真实页码引用的解析、索引、对齐与无页码兜底回归。"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tools import _legacy
from indexing.parser_router import ParsedDoc, SimplePDFParser
from rag.ingest import _load_pdf
from rag.store import ChromaStore


ROOT = Path(__file__).resolve().parents[1]
TARGET_PDF = ROOT / "kb" / "1_慢病食养指南" / "成人高血压食养指南（2023年版）.pdf"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _feature(text: str, length: int = 40) -> str:
    """从可检索正文里取一段稳定特征串。"""

    text = re.sub(r"^【上下文】.*?\n", "", text, flags=re.S)
    text = text.split("【完整知识单元】", 1)[0]
    normalized = _norm(text)
    if len(normalized) <= length:
        return normalized
    start = max(0, (len(normalized) - length) // 2)
    return normalized[start : start + length]


class PdfPageCitationTest(unittest.TestCase):
    def test_parser_preserves_real_page_numbers(self):
        parsed = SimplePDFParser(backend="pdfplumber").parse(str(TARGET_PDF))

        self.assertEqual(parsed.metadata["page_count"], 56)
        self.assertEqual(len(parsed.pages), 56)
        self.assertEqual([number for number, _ in parsed.pages], list(range(1, 57)))
        self.assertTrue(all(text.strip() for _, text in parsed.pages))

    def test_indexed_pdf_chunks_have_aligned_pages(self):
        store = ChromaStore(persist_dir="kb/chroma")
        try:
            payload = store.collection.get(include=["documents", "metadatas"])
        except Exception as exc:
            self.skipTest(f"知识库索引不可用: {exc}")

        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        pdf_items = [
            (document, metadata)
            for document, metadata in zip(documents, metadatas)
            if (metadata or {}).get("content_type") == "pdf"
        ]
        self.assertTrue(pdf_items, "索引中没有 PDF chunk")
        self.assertTrue(
            all(metadata.get("page_start") is not None for _, metadata in pdf_items),
            "存在没有真实起始页码的 PDF chunk",
        )

        import pdfplumber

        checked = 0
        aligned = 0
        for document, metadata in pdf_items:
            if checked >= 20:
                break
            source = ROOT / "kb" / str(metadata["category"]) / str(metadata["doc"])
            if not source.is_file():
                continue
            feature = _feature(str(document or ""))
            if len(feature) < 12:
                continue
            page_number = int(metadata["page_start"])
            with pdfplumber.open(source) as pdf:
                page_text = _norm(pdf.pages[page_number - 1].extract_text() or "")
            checked += 1
            if feature in page_text:
                aligned += 1

        self.assertGreaterEqual(checked, 20, "可用于页码对齐校验的 chunk 不足 20 条")
        self.assertEqual(checked, aligned, f"页码对齐率 {aligned}/{checked}")

    def test_pdf_without_pages_never_emits_fake_page(self):
        class _NoPageParser:
            def parse(self, _path: str) -> ParsedDoc:
                return ParsedDoc(
                    text="真实正文，但没有可靠页码。",
                    metadata={"page_count": 1, "parser_used": "test"},
                )

        class _Decision:
            parser_class = _NoPageParser
            reason = "测试：无可靠页码"

            class parser_type:
                value = "simple"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no-page.pdf"
            path.write_bytes(b"%PDF-1.4\n")
            with patch("indexing.parser_router.route", return_value=_Decision()):
                chunks, skipped = _load_pdf(
                    path,
                    max_chars=1200,
                    overlap_chars=120,
                    preview_dir=None,
                )

        self.assertFalse(skipped)
        self.assertEqual(len(chunks), 1)
        metadata = chunks[0].metadata
        self.assertIsNone(metadata["page_start"])
        self.assertIsNone(metadata["page_end"])
        self.assertNotRegex(metadata["anchor"], r"_p\d+$")
        self.assertEqual(
            _legacy._resolve_section(metadata["doc"], metadata["anchor"]),
            metadata["anchor"],
        )
        self.assertNotIn("第", _legacy._resolve_section(metadata["doc"], metadata["anchor"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
