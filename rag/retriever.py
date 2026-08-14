"""在线检索层。

Agent 只依赖本文件的 ``search``，不依赖 Chroma 的 API。

检索增强（对照桌面 RAG.md）：
- 元数据过滤（第 5.1 讲）：``filter`` 参数映射到 Chroma 的 ``where``。
- 混合检索 BM25+RRF（第 5.2 讲）：稠密向量 + BM25 关键词，RRF 融合。
- 重排 cross-encoder（第 6 讲）：融合后用 ``bge-reranker-v2-m3`` 重排。
- 查询转换（第 5.4 讲）：可选注入 ``transform``，见 ``rag/query_transform.py``。

全部增强项均可独立开关，且对缺失配置 / 模型加载失败做降级，
不依赖 configs.py 也能用默认值跑起来。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional, Sequence

import numpy as np

from .models import RetrievalHit, SearchResult
from .store import ChromaStore, DEFAULT_COLLECTION


# 检索增强配置：默认值；若 configs.KB_CONFIG 存在则覆盖同名键（不强制依赖 configs）。
RETRIEVAL_CONFIG: dict[str, Any] = {
    "kb_dir": "kb/chroma",
    "collection_name": DEFAULT_COLLECTION,
    "embedding_backend": "bge",
    "enable_hybrid": True,            # BM25 + 稠密向量 RRF 融合
    "enable_rerank": True,            # cross-encoder 重排（不可用时自动跳过）
    "rerank_model": "BAAI/bge-reranker-v2-m3",
    "rerank_top_k": 10,               # 先取候选数，再重排
    "rrf_k": 60,                      # RRF 常数
    "metadata_filter": None,          # 默认不过滤
}


def _load_config() -> dict[str, Any]:
    cfg = dict(RETRIEVAL_CONFIG)
    try:
        from configs import KB_CONFIG

        cfg.update({k: v for k, v in KB_CONFIG.items() if k in RETRIEVAL_CONFIG})
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------------------
# BM25（纯 numpy 实现，无需额外依赖）
# ---------------------------------------------------------------------------
class _BM25:
    """Okapi BM25，用于混合检索的关键词召回分支。"""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [self._tokenize(d) for d in corpus]
        self.N = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.N) if self.N else 0.0
        self.df: dict[str, int] = {}
        for doc in self.docs:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1
        self.idf = {
            term: math.log(1.0 + (self.N - df + 0.5) / (df + 0.5))
            for term, df in self.df.items()
        }

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # 英文/数字按词，中文按字切（避免 jieba 依赖）
        return re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())

    def scores(self, query: str) -> list[float]:
        q_terms = self._tokenize(query)
        out: list[float] = []
        for doc in self.docs:
            dl = len(doc)
            if dl == 0:
                out.append(0.0)
                continue
            freq: dict[str, int] = {}
            for t in doc:
                freq[t] = freq.get(t, 0) + 1
            score = 0.0
            for term in q_terms:
                if term not in self.idf:
                    continue
                f = freq.get(term, 0)
                if f == 0:
                    continue
                denom = f + self.k1 * (1.0 - self.b + self.b * dl / self.avgdl)
                score += self.idf[term] * (f * (self.k1 + 1.0)) / denom
            out.append(score)
        return out


# ---------------------------------------------------------------------------
# 检索器
# ---------------------------------------------------------------------------
class KnowledgeBaseRetriever:
    """面向业务的知识库检索器。"""

    def __init__(
        self,
        *,
        kb_dir: str | None = None,
        collection_name: str | None = None,
        embedding_backend: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        cfg = config or _load_config()
        self.cfg = cfg
        try:
            from configs import KB_CONFIG

            base: dict[str, Any] = dict(KB_CONFIG)
        except Exception:
            base = {}
        self.store = ChromaStore(
            persist_dir=kb_dir or cfg.get("kb_dir", base.get("kb_dir", "kb/chroma")),
            collection_name=collection_name
            or cfg.get("collection_name", base.get("collection_name", DEFAULT_COLLECTION)),
            embedding_backend=embedding_backend
            or cfg.get("embedding_backend", base.get("embedding_backend", "bge")),
        )
        self._bm25: Optional[_BM25] = None
        self._corpus_ids: list[str] = []
        self._reranker = None
        self._reranker_loaded = False

    # ---- BM25 索引（惰性构建，基于集合内全部文档） ----
    def _ensure_bm25(self) -> None:
        if self._bm25 is not None or self._corpus_ids:
            return
        try:
            data = self.store.collection.get(include=["documents", "ids"])
            docs = data.get("documents") or []
            ids = data.get("ids") or []
            if docs:
                self._bm25 = _BM25(docs)
                self._corpus_ids = [str(i) for i in ids]
        except Exception:
            self._bm25 = None
            self._corpus_ids = []

    # ---- 重排器（惰性加载，失败则置空，后续不再重试） ----
    def _ensure_reranker(self) -> None:
        if self._reranker_loaded:
            return
        self._reranker_loaded = True
        if not self.cfg.get("enable_rerank", True):
            return
        try:
            from sentence_transformers import CrossEncoder

            self._reranker = CrossEncoder(
                self.cfg.get("rerank_model", "BAAI/bge-reranker-v2-m3")
            )
        except Exception:
            self._reranker = None

    def search(
        self,
        query: str,
        n_results: int = 3,
        *,
        filter: Optional[dict] = None,
        use_hybrid: Optional[bool] = None,
        use_rerank: Optional[bool] = None,
        transform: Optional[Callable[[str], Sequence[str]]] = None,
    ) -> SearchResult:
        try:
            # 1) 查询转换（可选）：改写 / 多查询 / HyDE
            queries = list(transform(query)) if transform else [query]
            queries = [q for q in queries if q and q.strip()] or [query]
            main_query = queries[0]

            # 2) 稠密向量检索（多查询时跨查询 RRF 合并，提升 recall）
            candidate_pool = max(int(n_results), int(self.cfg.get("rerank_top_k", 10)))
            k = float(self.cfg.get("rrf_k", 60))
            if len(queries) > 1:
                query_hit_lists: list[list[RetrievalHit]] = []
                for q in queries:
                    hits = self.store.search(q, n_results=candidate_pool)
                    if hits:
                        query_hit_lists.append(hits)
                dense_hits = (
                    self._merge_query_hits(query_hit_lists, k=k)
                    if query_hit_lists
                    else []
                )
            else:
                dense_hits = self.store.search(main_query, n_results=candidate_pool)
            if not dense_hits:
                return SearchResult(query=query, hits=[])

            # 3) 混合检索：BM25 + 稠密，RRF 融合
            use_hybrid = (
                self.cfg.get("enable_hybrid", True) if use_hybrid is None else use_hybrid
            )
            fused = self._hybrid_fuse(main_query, dense_hits) if use_hybrid else dense_hits

            # 4) 元数据过滤
            meta_filter = filter or self.cfg.get("metadata_filter")
            if meta_filter:
                fused = [h for h in fused if self._match_filter(h.metadata, meta_filter)]

            # 5) 重排（候选多于目标时才重排）
            use_rerank = (
                self.cfg.get("enable_rerank", True) if use_rerank is None else use_rerank
            )
            if use_rerank and len(fused) > n_results:
                fused = self._rerank(query, fused)

            return SearchResult(query=query, hits=fused[:n_results])
        except Exception as exc:
            return SearchResult(query=query, error=str(exc))

    def _hybrid_fuse(self, query: str, dense_hits: list[RetrievalHit]) -> list[RetrievalHit]:
        self._ensure_bm25()
        k = float(self.cfg.get("rrf_k", 60))
        # 稠密排名（distance 越小越相关）
        dense_rank = {
            h.chunk_id: i + 1
            for i, h in enumerate(sorted(dense_hits, key=lambda x: x.distance))
        }
        # BM25 排名（score 越大越相关）
        bm25_scores = (
            self._bm25.scores(query)
            if self._bm25 is not None
            else [0.0] * len(self._corpus_ids)
        )
        bm25_rank: dict[str, int] = {}
        if self._bm25 is not None and self._corpus_ids:
            ranked = sorted(
                (
                    (s, cid)
                    for s, cid in zip(bm25_scores, self._corpus_ids)
                    if s > 0.0
                ),
                key=lambda x: -x[0],
            )
            bm25_rank = {cid: i + 1 for i, (_, cid) in enumerate(ranked)}

        hit_by_id = {h.chunk_id: h for h in dense_hits}
        fused_scores: dict[str, float] = {}
        for cid in set(dense_rank) | set(bm25_rank):
            if cid not in hit_by_id:
                continue
            s = 0.0
            if cid in dense_rank:
                s += 1.0 / (k + dense_rank[cid])
            if cid in bm25_rank:
                s += 1.0 / (k + bm25_rank[cid])
            fused_scores[cid] = s
        ordered = sorted(fused_scores, key=lambda c: -fused_scores[c])
        return [hit_by_id[c] for c in ordered]

    def _rerank(self, query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        self._ensure_reranker()
        if self._reranker is None:
            return hits
        try:
            pairs = [(query, h.text) for h in hits]
            scores = self._reranker.predict(pairs)
            ranked = sorted(zip(hits, scores), key=lambda x: -float(x[1]))
            return [h for h, _ in ranked]
        except Exception:
            return hits

    @staticmethod
    def _match_filter(metadata: dict[str, Any], flt: dict[str, Any]) -> bool:
        for key, val in flt.items():
            if metadata.get(key) != val:
                return False
        return True

    @staticmethod
    def _merge_query_hits(
        query_hit_lists: list[list[RetrievalHit]], k: float = 60.0
    ) -> list[RetrievalHit]:
        """跨多个查询的稠密检索结果做 RRF 融合（查询转换场景用）。"""

        fused: dict[str, float] = {}
        by_id: dict[str, RetrievalHit] = {}
        for hits in query_hit_lists:
            ranked = sorted(hits, key=lambda x: x.distance)
            for rank, h in enumerate(ranked):
                cid = h.chunk_id
                by_id.setdefault(cid, h)
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
        ordered = sorted(fused, key=lambda c: -fused[c])
        return [by_id[c] for c in ordered]


_DEFAULT_RETRIEVER: KnowledgeBaseRetriever | None = None


def get_retriever() -> KnowledgeBaseRetriever:
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None:
        _DEFAULT_RETRIEVER = KnowledgeBaseRetriever()
    return _DEFAULT_RETRIEVER


def search(
    query: str,
    n_results: int = 3,
    *,
    filter: Optional[dict] = None,
    use_hybrid: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    transform: Optional[Callable[[str], Sequence[str]]] = None,
) -> SearchResult:
    return get_retriever().search(
        query,
        n_results=n_results,
        filter=filter,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
        transform=transform,
    )
