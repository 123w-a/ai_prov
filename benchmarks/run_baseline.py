"""Run an offline baseline against the project's real PDF corpus.

Usage: .venv\\Scripts\\python.exe benchmarks\\run_baseline.py --offline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexing.parser_router import route, SimplePDFParser
from nutrition_rules import audit, detect_conditions
from benchmarks.historical_cases import CASES


EXPECTED_PDF_COUNT = 27
MIN_TOTAL_PAGES = 682
MIN_TOTAL_CHARS = 443322


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="reject network-dependent benchmark paths")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.offline:
        parser.error("--offline is required for the reproducible baseline")

    pdfs = sorted((ROOT / "kb_corpus").rglob("*.pdf"))
    documents = []
    failures = []
    started = time.perf_counter()
    for path in pdfs:
        item_started = time.perf_counter()
        try:
            decision = route(str(path))
            parsed = SimplePDFParser(backend="pymupdf").parse(str(path))
            documents.append({
                "file": str(path.relative_to(ROOT)),
                "pages": parsed.metadata.get("page_count", 0),
                "chars": len(parsed.text),
                "parser": parsed.metadata.get("parser_used"),
                "route": decision.parser_type.value,
                "elapsed_ms": round((time.perf_counter() - item_started) * 1000, 2),
            })
        except Exception as exc:
            failures.append({"file": str(path.relative_to(ROOT)), "error": str(exc)})

    regressions = []
    for case in CASES:
        conditions = detect_conditions(case["user"])
        violations = audit(case["answer"], case["conditions"])
        keywords = {item["keyword"] for item in violations}
        regressions.append({
            "id": case["id"],
            "conditions_ok": conditions == case["conditions"],
            "required_keywords_ok": all(k in keywords for k in case["required_keywords"]),
            "violation_count": len(violations),
            "clean_ok": bool(case["required_keywords"]) or not violations,
            "violations": sorted(keywords),
        })

    corpus_ok = (
        len(pdfs) == EXPECTED_PDF_COUNT
        and len(documents) == EXPECTED_PDF_COUNT
        and not failures
        and all(item["pages"] > 0 and item["chars"] > 0 for item in documents)
        and sum(item["pages"] for item in documents) >= MIN_TOTAL_PAGES
        and sum(item["chars"] for item in documents) >= MIN_TOTAL_CHARS
    )
    report = {
        "schema": "ai-prov.offline-baseline.v1",
        "corpus": {
            "root": "kb_corpus",
            "pdf_count": len(pdfs),
            "parsed_count": len(documents),
            "failure_count": len(failures),
            "total_pages": sum(item["pages"] for item in documents),
            "total_chars": sum(item["chars"] for item in documents),
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "documents": documents,
        "failures": failures,
        "corpus_ok": corpus_ok,
        "historical_regressions": regressions,
        "historical_passed": all(r["conditions_ok"] and r["required_keywords_ok"] and r["clean_ok"] for r in regressions),
        "network_used": False,
        "passed": corpus_ok and all(r["conditions_ok"] and r["required_keywords_ok"] and r["clean_ok"] for r in regressions),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
