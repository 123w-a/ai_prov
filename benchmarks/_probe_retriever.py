import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag.retriever import KnowledgeBaseRetriever

kb = KnowledgeBaseRetriever()
q = "甲状腺结节术后能不能吃海带紫菜"
r = kb.search(q, n_results=5)
hits = getattr(r, "hits", None) or (r.get("hits") if isinstance(r, dict) else [])
out = {"query": q, "hit_count": len(hits), "snippets": [str(getattr(h, "text", None) or (h.get("text", "") if isinstance(h, dict) else ""))[:120] for h in hits[:5]]}
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
print(json.dumps(out, ensure_ascii=True, indent=1))
