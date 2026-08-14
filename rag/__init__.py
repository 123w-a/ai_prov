"""可复用的 RAG 基础设施。

业务代码只需要调用 ``rag.retriever.search``，不需要知道 Chroma、
Embedding 或切片的实现细节。
"""

from .models import Chunk, RetrievalHit, SearchResult

__all__ = ["Chunk", "RetrievalHit", "SearchResult"]
