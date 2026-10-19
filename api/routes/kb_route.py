"""知识库检索接口：评测编译门与未来前端共用同一个 retriever client，
消除评测旁路直连 chroma 与生产进程的互斥冲突（2026-08-28 实证）。

2026-10-19 增 `/kb/excerpt`：结果页右栏「权威文件」点开时要看那一页的原文。
**只读、只按 (文件名, 章节锚点) 精确取回入库时的那一段**，不做新的检索排名，
也不拼接邻段——用户点开的是「这道菜真正用的那一页」，不是"相关的几段"。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

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


class KbExcerptRequest(BaseModel):
    # 文件名（与入库时的 metadata["source"] 同一个值：kb/ 下的 path.name）
    source: str = Field(..., min_length=1, max_length=200)
    # 章节锚点：入库时写的是 `{文件名}_p{页码}`，取不到页码时是 `{文件名}_c{切片序号}`。
    # 允许为空——为空时只按文件名取第一段，此时 found 也会如实说明"没有精确定位"。
    section: str = Field(default="", max_length=200)
    # 单段上限：这一栏是"核对用的原文"，不是把整本指南铺进右栏。
    max_chars: int = Field(default=900, ge=120, le=4000)


def _first(result: dict) -> tuple[str, dict, str]:
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []
    ids = result.get("ids") or []
    text = str(docs[0]) if docs else ""
    meta = dict(metas[0]) if metas and isinstance(metas[0], dict) else {}
    cid = str(ids[0]) if ids else ""
    return text, meta, cid


@router.post("/kb/excerpt")
def kb_excerpt(req: KbExcerptRequest):
    """按 (文件名, 章节锚点) 取回入库时的那段原文。

    三种如实结局，前端各有一种说法：
      · exact    —— 文件名 + 锚点都对上，这是这一页的原文；
      · fallback —— 锚点没对上，退回同文件的首段（说明 precise=false，别声称是那一页）；
      · missing  —— 库里没有这个文件（例如模型自填了不存在的文件名），一个字都不编。
    """
    source = req.source.strip()
    section = req.section.strip()
    try:
        collection = get_retriever().store.collection
    except Exception as exc:  # 集合打不开时说清是环境问题，不冒充"没有这段"
        return {"found": False, "reason": f"知识库不可用：{exc}", "precise": False}

    where: dict | None = None
    if section:
        where = {"$and": [{"source": source}, {"anchor": section}]}
    try:
        got = collection.get(where=where, limit=1, include=["documents", "metadatas"]) if where else collection.get(
            where={"source": source}, limit=1, include=["documents", "metadatas"]
        )
    except Exception as exc:
        return {"found": False, "reason": f"按锚点取原文失败：{exc}", "precise": False, "source": source, "section": section}

    text, meta, chunk_id = _first(got or {})
    precise = True
    if not text and section:
        # 锚点没命中：可能是模型自填的章节名而不是入库锚点，退回同文件首段并标明不精确。
        try:
            got = collection.get(where={"source": source}, limit=1, include=["documents", "metadatas"])
            text, meta, chunk_id = _first(got or {})
            precise = False
        except Exception as exc:
            return {"found": False, "reason": f"按文件取原文失败：{exc}", "precise": False, "source": source, "section": section}

    if not text:
        return {"found": False, "reason": "知识库里没有这份文件", "precise": False, "source": source, "section": section}

    clipped = text[: req.max_chars].rstrip()
    return {
        "found": True,
        "precise": precise,
        "source": str(meta.get("source") or source),
        "section": str(meta.get("anchor") or section),
        "page_start": meta.get("page_start"),
        "page_end": meta.get("page_end"),
        "chunk_id": chunk_id,
        "text": clipped,
        "truncated": len(text) > len(clipped),
        "total_chars": len(text),
    }
