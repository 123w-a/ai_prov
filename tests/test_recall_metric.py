"""Recall@k 度量内核的确定性回归测试（不依赖模型与索引）。"""

import unittest
from pathlib import Path

from benchmarks.recall import evaluate_recall, index_ready
from benchmarks.retrieval_cases import CASES


def _fake_retriever(mapping: dict[str, list[str]]):
    return lambda query: mapping.get(query, [])


class RecallMetricTest(unittest.TestCase):
    def test_full_recall_when_keywords_in_top_k(self):
        mapping = {
            "q1": ["含嘌呤的文档", "尿酸说明"],
            "q2": ["提到胎盘", "酮体出现"],
        }
        cases = [
            {"id": "a", "query": "q1", "expect_keywords": ["嘌呤", "尿酸"]},
            {"id": "b", "query": "q2", "expect_keywords": ["胎盘"]},
        ]

        report = evaluate_recall(cases, _fake_retriever(mapping), k=2)

        self.assertEqual(report["recall"], 1.0)
        self.assertTrue(all(c["hit"] for c in report["cases"]))

    def test_keyword_beyond_k_window_counts_as_miss(self):
        cases = [{"id": "a", "query": "q1", "expect_keywords": ["关键词"]}]

        report = evaluate_recall(cases, _fake_retriever({"q1": ["无关", "无关"]}), k=2)

        self.assertEqual(report["recall"], 0.0)
        self.assertEqual(report["cases"][0]["missing"], ["关键词"])

    def test_partial_recall_is_fraction(self):
        cases = [
            {"id": "a", "query": "q1", "expect_keywords": ["命中"]},
            {"id": "b", "query": "q2", "expect_keywords": ["落空"]},
        ]
        mapping = {"q1": ["命中文档"], "q2": ["别的"]}

        report = evaluate_recall(cases, _fake_retriever(mapping), k=1)

        self.assertEqual(report["recall"], 0.5)

    def test_real_case_table_shapes_are_complete(self):
        for case in CASES:
            self.assertIn("id", case)
            self.assertIn("query", case)
            self.assertTrue(case["expect_keywords"], case["id"])

    def test_index_ready_false_for_missing_or_empty(self):
        self.assertFalse(index_ready(Path(__file__).with_name(".no-such-index")))
        empty = Path(__file__).with_name(".empty-index-dir")
        empty.mkdir(exist_ok=True)
        try:
            self.assertFalse(index_ready(empty))
        finally:
            empty.rmdir()


if __name__ == "__main__":
    unittest.main(verbosity=2)
