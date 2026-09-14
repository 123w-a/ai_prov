"""Compare fixed paragraph chunking against deterministic routed chunking.

Usage:
    .venv\\Scripts\\python.exe benchmarks\\chunking_route_compare.py            # 默认 n=30 扩充集
    .venv\\Scripts\\python.exe benchmarks\\chunking_route_compare.py --cases legacy  # 旧的 4 条

说明：4 条用例只能反映个例波动，不足以支撑"路由切法是否更好"的结论；
默认改用 benchmarks/retrieval_cases_v2.py 的 30 条（关键词均已用
benchmarks/verify_cases_source.py 在原文核验过）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.recall import evaluate_recall
import rag.ingest as ingest
from rag.retriever import KnowledgeBaseRetriever
from rag.store import ChromaStore


K = 3


def _fixed_paragraph_chunks():
    original = ingest.choose_chunker
    ingest.choose_chunker = lambda _text, _meta: (
        "paragraph",
        "benchmark baseline: fixed paragraph chunking",
    )
    try:
        chunks, _, _, _ = ingest.load_chunks(
            "kb",
            max_chars=1200,
            overlap_chars=120,
        )
        return chunks
    finally:
        ingest.choose_chunker = original


def _build_retriever(chunks, tmp_dir: Path, collection_name: str):
    store = ChromaStore(
        persist_dir=tmp_dir,
        collection_name=collection_name,
        embedding_backend="bge",
    )
    store.rebuild(chunks)
    return KnowledgeBaseRetriever(
        kb_dir=str(tmp_dir),
        collection_name=collection_name,
        embedding_backend="bge",
        config={
            "kb_dir": str(tmp_dir),
            "collection_name": collection_name,
            "embedding_backend": "bge",
            "enable_hybrid": True,
            "enable_rerank": False,
            "rerank_top_k": 10,
            "rrf_k": 60,
        },
    )


def _retrieve(retriever: KnowledgeBaseRetriever, query: str) -> list[str]:
    result = retriever.search(
        query,
        n_results=K,
        use_hybrid=True,
        use_rerank=False,
    )
    if result.error:
        raise RuntimeError(result.error)
    return [hit.text for hit in result.hits]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        choices=("v2", "legacy"),
        default="v2",
        help="v2 = 30 条扩充集（默认）；legacy = 早期 4 条",
    )
    args = parser.parse_args()
    if args.cases == "v2":
        from benchmarks.retrieval_cases_v2 import RETRIEVAL_CASES_V2 as cases

        suffix = "_v2"
    else:
        from benchmarks.retrieval_cases import CASES as cases

        suffix = "_legacy"
    csv_path = ROOT / "benchmarks" / f"chunking_route_comparison{suffix}.csv"
    json_path = ROOT / "benchmarks" / f"chunking_route_comparison{suffix}.json"

    fixed_chunks = _fixed_paragraph_chunks()
    routed_chunks, _, _, _ = ingest.load_chunks(
        "kb",
        max_chars=1200,
        overlap_chars=120,
    )

    with tempfile.TemporaryDirectory(
        prefix="ai-prvo-route-",
        ignore_cleanup_errors=True,
    ) as tmp:
        tmp_path = Path(tmp)
        fixed_retriever = _build_retriever(
            fixed_chunks,
            tmp_path / "fixed",
            "fixed_paragraph",
        )
        routed_retriever = _build_retriever(
            routed_chunks,
            tmp_path / "routed",
            "routed_chunks",
        )
        fixed_report = evaluate_recall(
            cases,
            lambda query: _retrieve(fixed_retriever, query),
            k=K,
        )
        routed_report = evaluate_recall(
            cases,
            lambda query: _retrieve(routed_retriever, query),
            k=K,
        )

    parent_chunks = [
        chunk
        for chunk in routed_chunks
        if chunk.metadata.get("chunk_strategy") == "parent_child"
    ]
    complete_parent_chunks = [
        chunk
        for chunk in parent_chunks
        if chunk.metadata.get("is_complete_parent")
    ]
    completeness = (
        len(complete_parent_chunks) / len(parent_chunks) if parent_chunks else 1.0
    )
    restricted_rate = 1.0 - completeness
    recall_delta = round(routed_report["recall"] - fixed_report["recall"], 4)

    fixed_by_id = {item["id"]: item for item in fixed_report["cases"]}
    routed_by_id = {item["id"]: item for item in routed_report["cases"]}
    rows = [
        {
            "record_type": "summary",
            "key": "recall_at_3",
            "baseline": fixed_report["recall"],
            "route": routed_report["recall"],
            "delta": recall_delta,
            "note": "same cases, hybrid retrieval, rerank disabled",
        },
        {
            "record_type": "summary",
            "key": "slice_complete_rate",
            "baseline": "",
            "route": round(completeness, 4),
            "delta": "",
            "note": "atomic parent-child chunks whose full parent is retained",
        },
        {
            "record_type": "summary",
            "key": "restricted_rate",
            "baseline": "",
            "route": round(restricted_rate, 4),
            "delta": "",
            "note": "1 - slice_complete_rate for routed parent-child chunks",
        },
        {
            "record_type": "summary",
            "key": "chunk_count",
            "baseline": len(fixed_chunks),
            "route": len(routed_chunks),
            "delta": len(routed_chunks) - len(fixed_chunks),
            "note": "indexed chunk count",
        },
    ]
    for case in cases:
        case_id = case["id"]
        fixed = fixed_by_id[case_id]
        routed = routed_by_id[case_id]
        rows.append(
            {
                "record_type": "case",
                "key": case_id,
                "baseline": fixed["hit"],
                "route": routed["hit"],
                "delta": int(routed["hit"]) - int(fixed["hit"]),
                "note": case["query"],
            }
        )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("record_type", "key", "baseline", "route", "delta", "note"),
        )
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "k": K,
        "fixed": fixed_report,
        "routed": routed_report,
        "recall_delta": recall_delta,
        "slice_complete_rate": round(completeness, 4),
        "restricted_rate": round(restricted_rate, 4),
        "chunk_count": {
            "fixed": len(fixed_chunks),
            "routed": len(routed_chunks),
        },
        "retrieval": "bge + hybrid RRF, rerank disabled",
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
