"""提示词编译门（薄壳）：sweep 用 python 检索，verdict 用 eval-runner bootstrapMetricLoop。

用法：
  python benchmarks/compile_prompt.py --phase sweep --side baseline   # 原句直检
  python benchmarks/compile_prompt.py --phase sweep --side candidate  # multi_query 变体多路
  python benchmarks/compile_prompt.py --phase verdict                 # node 裁决出红绿报告
铁律：本壳不改任何现网提示词；candidate 的变体实现必须来自隔离副本或用户指定。
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


def _http_search(query: str, k: int) -> list[str]:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        "http://127.0.0.1:8010/api/kb/search",
        data=json.dumps({"query": query, "k": k}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [str(h.get("text", "")) for h in data.get("hits", [])]

from benchmarks.retrieval_cases import CASES  # noqa: E402
from benchmarks.retrieval_cases_extended import EXTENDED_CASES  # noqa: E402


def _run_side(side: str) -> None:
    from rag._pilot_query_transform_variant import multi_query_variant

    llm = None
    if side == "candidate":
        from model_name import get_langchain_llm
        base = get_langchain_llm("deepseek", temperature=0.3, max_tokens=200)
        llm = lambda system, user: base.invoke([("system", system), ("human", user)]).content  # noqa: E731

    out = {"cases": [], "runs": {}}
    for c in list(CASES) + list(EXTENDED_CASES):
        texts = []
        if side == "baseline":
            texts = _http_search(c["query"], 5)
        else:
            seen: set[str] = set()
            for q in multi_query_variant(c["query"], llm, 3) if llm else [c["query"]]:
                for t in _http_search(q, 3):
                    if t and t not in seen:
                        seen.add(t)
                        texts.append(t)
        out["cases"].append({"id": c["id"], "input": c["query"], "expect_keywords": c["expect_keywords"]})
        out["runs"][c["query"]] = texts
        print(c["id"], len(texts), "docs")
    Path(__file__).with_name(f"sweep_{side}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SWEEP", side, "DONE")


def _verdict() -> None:
    driver = Path(__file__).with_name("verdict_driver.mjs")
    r = subprocess.run(["node", str(driver)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr[-800:])
        sys.exit(r.returncode)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["sweep", "verdict"], required=True)
    ap.add_argument("--side", choices=["baseline", "candidate"], default="baseline")
    a = ap.parse_args()
    _verdict() if a.phase == "verdict" else _run_side(a.side)
