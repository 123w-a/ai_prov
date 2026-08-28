"""Pure control: SAME budget as before (single route, n_results=5).
Only variable = query rewritten by variant prompt (first non-original variant).
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
    variants = multi_query_variant(q, llm, 3)
    pick = next((v for v in variants[1:] if v), q)
    r = kb.search(pick, n_results=5)
    return [str(getattr(h, "text", "") or "") for h in (getattr(r, "hits", None) or [])]

report = evaluate_recall(union, retrieve, 5)
Path(__file__).with_name("pure_union.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
for c in report["cases"]:
    print(c["id"], "hit=" + str(c["hit"]), "missing=" + ",".join(c["missing"]))
print("RECALL", report["recall"])