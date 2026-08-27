import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from indexing.parser_router import _sample_indices, route, ParserType


class ParserRouterBoundaryTest(unittest.TestCase):
    def test_sample_one_page_never_divides_by_zero(self):
        self.assertEqual(_sample_indices(10, 1), [0])
        self.assertEqual(_sample_indices(0, 1), [])

    def test_non_pdf_extension_is_not_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.p"
            path.write_text("plain text", encoding="utf-8")
            decision = route(str(path))
            self.assertEqual(decision.parser_type, ParserType.SIMPLE)
            self.assertEqual(decision.details["ext"], ".p")

    def test_force_parser_still_wins_before_file_probe(self):
        decision = route("missing.pdf", force_parser=ParserType.SIMPLE)
        self.assertTrue(decision.details["override"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
