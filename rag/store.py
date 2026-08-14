"""向量库适配层。

业务层不直接接触 Chroma 的 client、collection 和原始 query 字典。
未来替换为 Milvus、FAISS 或其他向量库时，只需要替换这一层。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .embeddings import create_embedding_function
from .models import Chunk, RetrievalHit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION = "dietary_kb"


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """把 metadata 变成 Chroma 支持的标量字典。"""

    result: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        else:
            result[str(key)] = json.dumps(value, ensure_ascii=False)
    return result


class ChromaStore:
    """Chroma 的最小适配器：重建、写入、打开、相似度检索。"""

    def __init__(
        self,
        persist_dir: str | Path,
        collection_name: str = DEFAULT_COLLECTION,
        embedding_backend: str = "bge",
    ):
        self.persist_dir = resolve_project_path(persist_dir)
        self.collection_name = collection_name
        self.embedding_backend = embedding_backend
        self._client = None
        self._collection = None

    def _get_client(self):
        if self._client is None:
            import chromadb

            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            embedding_function = create_embedding_function(self.embedding_backend)
            try:
                self._collection = self._get_client().get_collection(
                    self.collection_name,
                    embedding_function=embedding_function,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"知识库集合不可用: {self.collection_name} ({exc})"
                ) from exc
        return self._collection

    def rebuild(self, chunks: Iterable[Chunk]):
        client = self._get_client()
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass

        embedding_function = create_embedding_function(self.embedding_backend)
        self._collection = client.create_collection(
            self.collection_name,
            embedding_function=embedding_function,
            metadata={"embedding_backend": self.embedding_backend},
        )
        self.add(chunks)
        return self._collection

    def add(self, chunks: Iterable[Chunk]) -> int:
        items = list(chunks)
        if not items:
            return 0
        collection = self.collection
        collection.add(
            ids=[item.chunk_id for item in items],
            documents=[item.text for item in items],
            metadatas=[_safe_metadata(item.metadata) for item in items],
        )
        return len(items)

    def count(self) -> int:
        return int(self.collection.count())

    def search(self, query: str, n_results: int = 3) -> list[RetrievalHit]:
        if not query or not query.strip():
            return []
        collection = self.collection
        if collection.count() == 0:
            return []

        limit = max(1, min(int(n_results), collection.count()))
        result = collection.query(
            query_texts=[query.strip()],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[RetrievalHit] = []
        for chunk_id, document, metadata, distance in zip(
            ids, documents, metadatas, distances
        ):
            hits.append(
                RetrievalHit(
                    chunk_id=str(chunk_id),
                    text=str(document or ""),
                    metadata=dict(metadata or {}),
                    distance=float(distance),
                )
            )
        return hits
