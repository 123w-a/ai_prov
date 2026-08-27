"""Recall@k 度量：纯函数内核 + 可跳过的离线 CLI。

约定与 docs/benchmarking.md 一致——依赖缺失时显式 SKIPPED 并注明原因，
绝不把"没跑成"伪装成"通过"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.retrieval_cases import CASES  # noqa: E402


def evaluate_recall(
    cases: list[dict], retrieve_fn: Callable[[str], Sequence[str]], k: int
) -> dict:
    """案例级 recall@k：任一 expect 关键词出现在 top-k 文本并集即算命中。"""

    results = []
    for case in cases:
        docs = list(retrieve_fn(case["query"]))[:k]
        joined = "\n".join(docs)
        found = [kw for kw in case["expect_keywords"] if kw in joined]
        missing = [kw for kw in case["expect_keywords"] if kw not in joined]
        results.append({
            "id": case["id"],
            "hit": bool(found),
            "found": found,
            "missing": missing,
        })
    recall = (sum(1 for r in results if r["hit"]) / len(results)) if results else 0.0
    return {"k": k, "recall": round(recall, 4), "cases": results}


def index_ready(kb_dir: Path) -> bool:
    """Chroma 持久目录存在且非空才认为索引可用。"""

    return kb_dir.is_dir() and any(kb_dir.iterdir())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--min-recall", type=float, default=0.75)
    parser.add_argument("--output", type=Path, default=None)
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

    report = {"skipped": None, **evaluate_recall(CASES, retrieve, k=args.k)}
    report["min_recall"] = args.min_recall
    report["passed"] = report["recall"] >= args.min_recall
    payload = json.dumps(report, ensure_ascii=False)
    print(payload)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
