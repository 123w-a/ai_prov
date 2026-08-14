"""RAG 模块之间传递的数据结构。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Chunk:
    """经过清洗并准备写入向量库的一段文本。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalHit:
    """一次检索命中的标准结果。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any]
    distance: float

    @property
    def source(self) -> str:
        return str(self.metadata.get("source") or "未知来源")

    @property
    def section(self) -> str:
        return str(self.metadata.get("anchor") or "")


@dataclass(frozen=True)
class SearchResult:
    """统一的检索响应，避免业务层依赖 Chroma 原始字典。"""

    query: str
    hits: list[RetrievalHit] = field(default_factory=list)
    error: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.hits) and self.error is None
