"""Embedding 工厂层。

这里作为 ``rag.embeddings`` 包存在，是为了优先覆盖同名旧模块文件，
让建库和检索共用离线友好的 Embedding 创建逻辑。
"""

from __future__ import annotations

import os
from typing import Any


def create_embedding_function(backend: str | None = None) -> Any:
    """创建 Chroma 使用的 Embedding 函数。

    - ``onnx``：返回 ``None``，使用 Chroma 默认 embedding。
    - ``bge``：使用 sentence-transformers，并默认只读本地缓存。
    """

    selected = (backend or os.getenv("KB_EMBEDDING", "bge")).strip().lower()
    if selected in {"onnx", "default"}:
        return None
    if selected != "bge":
        raise ValueError(f"未知 embedding 后端: {selected}")

    # 避免网络受限时反复重试 HuggingFace。
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        from chromadb.utils import embedding_functions as ef
    except Exception as exc:
        raise RuntimeError(
            "使用 bge 需要 chromadb 和 sentence-transformers 依赖"
        ) from exc

    model_name = os.getenv("KB_BGE_MODEL", "BAAI/bge-small-zh-v1.5")
    normalize = os.getenv("KB_BGE_NORMALIZE", "true").lower() != "false"
    try:
        return ef.SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            normalize_embeddings=normalize,
            local_files_only=True,
        )
    except TypeError:
        # 兼容旧版 Chroma：没有 local_files_only 参数时，仍保留离线环境变量。
        return ef.SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            normalize_embeddings=normalize,
        )
    except Exception as exc:
        raise RuntimeError(
            f"bge 模型加载失败: {exc}；如果本机没有模型缓存，"
            "可临时设置 KB_EMBEDDING=onnx 后重新建库和检索。"
        ) from exc
