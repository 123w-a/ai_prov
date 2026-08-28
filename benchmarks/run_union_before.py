"""XiaoShanPilot BEFORE: 4 original + 5 challenge cases = 9-case union."""
import json, sys, io
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from rag.retriever import KnowledgeBaseRetriever
from benchmarks.retrieval_cases import CASES
from benchmarks.retrieval_cases_extended import EXTENDED_CASES
from benchmarks.recall import evaluate_recall

kb = KnowledgeBaseRetriever()
union = CASES + EXTENDED_CASES
report = evaluate_recall(union, lambda q: [str(getattr(h, "text", "")) for h in (kb.search(q, n_results=5).hits or [])], 5)
Path(__file__).with_name("before_union.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
for c in report["cases"]:
    print(c["id"], "hit=" + str(c["hit"]), "missing=" + ",".join(c["missing"]))
print("RECALL", report["recall"])
