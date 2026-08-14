"""Embedding 工厂层。"""

from __future__ import annotations

import os
from typing import Any


def create_embedding_function(backend: str | None = None) -> Any:
    """创建 Chroma 使用的 Embedding 函数。

    ``onnx`` 返回 ``None``，让 Chroma 使用其默认 embedding；
    ``bge`` 使用 sentence-transformers 的中文模型。
    """

    selected = (backend or os.getenv("KB_EMBEDDING", "bge")).strip().lower()
    if selected in {"onnx", "default"}:
        return None
    if selected != "bge":
        raise ValueError(f"未知 embedding 后端: {selected}")

    try:
        from chromadb.utils import embedding_functions as ef
    except Exception as exc:
        raise RuntimeError(
            "使用 bge 需要 chromadb 和 sentence-transformers 依赖"
        ) from exc

    model_name = os.getenv("KB_BGE_MODEL", "BAAI/bge-small-zh-v1.5")
    try:
        return ef.SentenceTransformerEmbeddingFunction(model_name=model_name)
    except Exception as exc:
        raise RuntimeError(f"bge 模型加载失败: {exc}") from exc
