"""XiaoShanPilot AFTER: variant multi_query on the 9-case union.
Budget note: after uses 1+variants routes x n_results=3 (before: 1 route x 5)
— wider recall surface, disclosed in the red/green report.
"""
import json, sys, io
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from rag.retriever import KnowledgeBaseRetriever
from rag._pilot_query_transform_variant import multi_query_variant
from benchmarks.retrieval_cases import CASES
from benchmarks.retrieval_cases_extended import EXTENDED_CASES
from benchmarks.recall import evaluate_recall
from model_name import get_langchain_llm

_base = get_langchain_llm("deepseek", temperature=0.3, max_tokens=200)
llm = lambda system, user: _base.invoke([("system", system), ("human", user)]).content

kb = KnowledgeBaseRetriever()
union = CASES + EXTENDED_CASES

def retrieve(q):
    seen, texts = set(), []
    for qq in multi_query_variant(q, llm, 3):
        r = kb.search(qq, n_results=3)
        for h in (getattr(r, "hits", None) or []):
            t = str(getattr(h, "text", "") or "")
            if t and t not in seen:
                seen.add(t); texts.append(t)
    return texts

report = evaluate_recall(union, retrieve, 5)
Path(__file__).with_name("after_union.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
for c in report["cases"]:
    print(c["id"], "hit=" + str(c["hit"]), "missing=" + ",".join(c["missing"]))
print("RECALL", report["recall"])