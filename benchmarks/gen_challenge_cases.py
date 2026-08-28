"""Generate challenge retrieval cases: colloquial/typo/indirect queries.

Runs the REAL retriever over candidate challenge queries and dumps full top-k
hit texts so expect_keywords can be harvested from actual corpus hits (never
invented). Output: benchmarks/challenge_raw.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from rag.retriever import KnowledgeBaseRetriever

QUERIES = [
    {"id": "bp-oyster-sauce", "query": "血压高的人炒菜能多放蚝油吗"},
    {"id": "diabetes-only-wholegrain", "query": "糖尿病主食是不是只能吃粗粮"},
    {"id": "hyperthyroid-coffee", "query": "甲亢患者能不能喝咖啡"},
    {"id": "kidney-tofu-myth", "query": "肾不好要少吃豆腐对不对"},
    {"id": "kid-fever-egg", "query": "孩子发烧能不能吃鸡蛋"},
    {"id": "anemia-dates-typo", "query": "貧血吃紅棗管用嗎"},
]

def main():
    kb = KnowledgeBaseRetriever()
    out = []
    for c in QUERIES:
        r = kb.search(c["query"], n_results=5)
        hits = getattr(r, "hits", []) or []
        texts = []
        for h in hits:
            t = getattr(h, "text", None) or (h.get("text", "") if isinstance(h, dict) else "")
            texts.append(str(t)[:500])
        out.append({"id": c["id"], "query": c["query"], "hit_count": len(hits), "hit_texts": texts})
    Path(__file__).with_name("challenge_raw.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("HARVESTED", sum(o["hit_count"] for o in out), "hits across", len(out), "queries")

if __name__ == "__main__":
    main()
