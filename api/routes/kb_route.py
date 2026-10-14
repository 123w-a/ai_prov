"""知识库检索接口：评测编译门与未来前端共用同一个 retriever client，
消除评测旁路直连 chroma 与生产进程的互斥冲突（2026-08-28 实证）。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from rag.retriever import get_retriever

router = APIRouter()


class KbSearchRequest(BaseModel):
    query: str
    k: int = 5


@router.post("/kb/search")
def kb_search(req: KbSearchRequest):
    r = get_retriever().search(req.query, n_results=max(1, min(req.k, 20)))
    hits = [
        {"text": str(getattr(h, "text", "") or ""), "distance": float(getattr(h, "distance", 0) or 0)}
        for h in (getattr(r, "hits", None) or [])
    ]
    return {"query": req.query, "count": len(hits), "hits": hits}
