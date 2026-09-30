"""未归一过敏原日志统计脚本的单元测试。"""

import tempfile
import unittest
from pathlib import Path

from scripts.allergen_unresolved_stats import summarize_unresolved_log


class AllergenUnresolvedStatsTest(unittest.TestCase):
    def test_counts_expressions_and_ignores_blank_or_malformed_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "allergen_unresolved.log"
            path.write_text(
                "2026-09-11T18:53:09\t乳过敏\n"
                "2026-09-11T18:53:38\t乳过敏\n"
                "2026-09-11T18:54:04\t完全未知的忌口\n"
                "\n"
                "# 注释\n",
                encoding="utf-8",
            )

            rows = summarize_unresolved_log(path)

        self.assertEqual(
            rows,
            [("乳过敏", 2), ("完全未知的忌口", 1)],
        )

    def test_limit_returns_highest_frequency_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "allergen_unresolved.log"
            path.write_text(
                "a\t较少\n"
                "b\t最多\n"
                "c\t最多\n"
                "f\t最多\n"
                "d\t中间\n"
                "e\t中间\n",
                encoding="utf-8",
            )

            rows = summarize_unresolved_log(path, limit=1)

        self.assertEqual(rows, [("最多", 3)])

    def test_missing_log_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "missing.log"
            self.assertEqual(summarize_unresolved_log(path), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
