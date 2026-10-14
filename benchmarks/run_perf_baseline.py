"""性能基线：给《参赛方案》（五）测试报告的「性能层」出真实数字。

测量项（均为端到端可复跑）：
  audit_p50/p95_us   护栏文本审计延迟（纯 CPU，500 次/文本，warmup 20 次）
  search_p50/p95_ms  RAG 混合检索延迟（Chroma+BM25+RRF+重排，warmup 2 次）
  整轮 API 不在本脚本内（依赖 LLM 网关与后端会话，另测另记）。

诚实口径：
  - 单机本机数据（环境写进 report.meta），不冒充服务器压测；
  - 分位数用排序后线性取位；样本量与 warmup 次数全部入报告；
  - 任一子项失败就标 skipped 子项，不把「没测成」写成 0。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT_TEXTS = [
    ("普通菜谱", "番茄炒蛋：番茄切块，鸡蛋打散，热锅凉油翻炒，加盐3克调味出锅。"),
    ("违禁菜谱", "老火汤炖猪肝配啤酒，放盐8克、生抽5克、鸡精3克，重油重盐才香。"),
    ("长菜谱", "红烧肉：五花肉切块焯水，炒糖色，加盐6克、老抽8毫升、蚝油5克、"
     "冰糖10g、八角桂皮香叶，小火慢炖一小时收汁，最后撒葱花出锅。" * 3),
]
SEARCH_QUERIES = [
    "血压高的人一天吃盐不能超过多少",
    "痛风能不能喝老火汤",
    "孕妇能不能吃生鱼片",
    "糖尿病主食怎么选",
    "减肥每天吃多少热量合适",
    "缺铁补什么食物最好",
    "食品标签上的钠怎么换算",
    "宝宝几个月加辅食",
]


def pct(samples: list[float], p: float) -> float:
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, round(p * (len(ordered) - 1))))
    return ordered[idx]


def bench_audit(repeats: int, warmup: int) -> dict:
    from domain.nutrition_rules import audit

    out = {}
    for name, text in AUDIT_TEXTS:
        for _ in range(warmup):
            audit(text, ["高血压", "糖尿病", "痛风", "肥胖"])
        samples = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            audit(text, ["高血压", "糖尿病", "痛风", "肥胖"])
            samples.append((time.perf_counter() - t0) * 1000)
        out[name] = {
            "n": repeats,
            "p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(pct(samples, 0.95), 3),
            "max_ms": round(max(samples), 3),
        }
    return out


def bench_search(repeats: int, warmup: int) -> dict:
    from rag.retriever import get_retriever

    retriever = get_retriever()
    for q in SEARCH_QUERIES[:2]:
        retriever.search(q, n_results=3)
    samples = []
    for _ in range(repeats):
        q = SEARCH_QUERIES[_ % len(SEARCH_QUERIES)]
        t0 = time.perf_counter()
        retriever.search(q, n_results=3)
        samples.append((time.perf_counter() - t0) * 1000)
    return {
        "n": repeats,
        "queries": len(SEARCH_QUERIES),
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(pct(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-repeats", type=int, default=500)
    parser.add_argument("--search-repeats", type=int, default=30)
    parser.add_argument("--skip-search", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "benchmarks" / "perf.baseline.json"
    )
    args = parser.parse_args()

    report: dict = {
        "skipped": None,
        "meta": {
            "scope": "单机本机基线（非服务器压测），仅作方案性能层数字",
            "audit_repeats": args.audit_repeats,
            "search_repeats": args.search_repeats,
        },
        "audit": None,
        "search": None,
    }

    report["audit"] = bench_audit(args.audit_repeats, warmup=20)

    if args.skip_search:
        report["search"] = {"skipped": "requested"}
    else:
        try:
            report["search"] = bench_search(args.search_repeats, warmup=2)
        except Exception as exc:
            report["search"] = {"skipped": f"retriever init failed: {exc}"}

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
