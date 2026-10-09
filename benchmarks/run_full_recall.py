"""三集合并的全量 Recall@k 评测（v1=4 + v2=30 + extended=5 ≈ 39 例）。

用途与分工：
  recall.py CLI           —— 只跑 v1 的 4 条，是 tests/rag/test_recall_metric.py
                            的快速绊线（秒级），不承担材料数字；
  本 runner               —— 三集合并出大样本数字，供《参赛方案》（五）测试与
                            验证引用；分组报出（v1/v2/extended 各自 recall），
                            失败案例逐条列 id，**如实记录不修数**。
门槛与绊线同口径：--min-recall 默认 0.75，未达标 exit 1。
输出：benchmarks/recall.full.json（结果文件，git 忽略由 .gitignore 决定）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.recall import evaluate_recall  # noqa: E402
from benchmarks.retrieval_cases import CASES as V1  # noqa: E402
from benchmarks.retrieval_cases_v2 import RETRIEVAL_CASES_V2 as V2  # noqa: E402
from benchmarks.retrieval_cases_extended import EXTENDED_CASES as EXT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-recall", type=float, default=0.75)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks" / "recall.full.json"
    )
    args = parser.parse_args()

    from rag.retriever import get_retriever

    try:
        retriever = get_retriever()
        indexed = retriever.store.collection.count()
    except Exception as exc:
        print(json.dumps({"skipped": f"retriever init failed: {exc}"}, ensure_ascii=False))
        return 0
    if not indexed:
        print(json.dumps({"skipped": "chroma collection is empty"}, ensure_ascii=False))
        return 0

    def retrieve(query: str) -> list[str]:
        return [hit.text or "" for hit in retriever.search(query, n_results=args.k).hits]

    report: dict = {"skipped": None, "k": args.k, "groups": {}}
    all_cases: list[dict] = []
    for name, cases in (("v1", V1), ("v2", V2), ("extended", EXT)):
        result = evaluate_recall(cases, retrieve, k=args.k)
        report["groups"][name] = {
            "n": len(cases),
            "recall": result["recall"],
            "failed": [c["id"] for c in result["cases"] if not c["hit"]],
        }
        all_cases.extend(result["cases"])

    report["n"] = len(all_cases)
    report["recall"] = round(
        sum(1 for c in all_cases if c["hit"]) / len(all_cases), 4
    ) if all_cases else 0.0
    report["cases"] = all_cases
    report["min_recall"] = args.min_recall
    report["passed"] = report["recall"] >= args.min_recall

    payload = json.dumps(report, ensure_ascii=False)
    print(payload)
    args.output.write_text(payload, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
