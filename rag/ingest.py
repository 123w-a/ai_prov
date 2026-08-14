"""离线索引流水线：加载 -> 清洗 -> 切片 -> 向量化 -> 写入向量库。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .chunking import chunk_by_heading, chunk_by_paragraph
from .cleaning import clean_text, parse_source
from .models import Chunk, SearchResult
from .store import ChromaStore, DEFAULT_COLLECTION, resolve_project_path


@dataclass(frozen=True)
class IndexBuildResult:
    store: ChromaStore
    chunks: list[Chunk]
    markdown_files: int
    pdf_files: int
    skipped_pdf_files: int


# Contextual Retrieval（Anthropic, 2024.9）：建库时为每个 chunk 生成一句
# 「它在全文中处于什么位置」的上下文物语，拼回原文再向量化，可显著降低
# 召回误判。contextual_llm 为注入式可调用对象，签名 llm(system, user) -> str。
# 不传 / 未开启时完全不生成前缀，行为与改造前一致。
_CONTEXTUAL_SYSTEM = (
    "你是知识库索引辅助器。给定从一篇膳食/营养文档中切出的一段文本，"
    "以及它所属文档的背景，请用一句极简中文（不超过 40 字）说明这段内容"
    "在全文中的上下文定位——它服务于哪条膳食原则、针对哪类人群或慢病。"
    "只输出这句说明本身，不要解释、不要加引号、不要换行。"
)


def _make_contextual_prefix(text: str, doc_context: str, contextual_llm) -> str:
    if contextual_llm is None:
        return ""
    try:
        prefix = (
            contextual_llm(_CONTEXTUAL_SYSTEM, f"{doc_context}\n\n【片段】\n{text[:500]}")
            or ""
        ).strip()
        prefix = prefix.strip("\"'。\n ")
        return prefix
    except Exception:
        return ""


def _load_markdown(
    path: Path,
    max_chars: int,
    overlap_chars: int,
    preview_dir: Path | None,
    contextual_llm: Optional[Callable[[str, str], str]] = None,
) -> list[Chunk]:
    cleaned = clean_text(path.read_text(encoding="utf-8"))
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / f"{path.stem}.clean.md").write_text(
            cleaned + "\n",
            encoding="utf-8",
        )

    doc_context = (
        f"文档：《{path.name}》，主题：面向慢病与健康人群的饮食营养与忌口原则。"
    )
    chunks: list[Chunk] = []
    for index, (title, body) in enumerate(
        chunk_by_heading(cleaned, max_chars=max_chars, overlap_chars=overlap_chars)
    ):
        text = f"【{title}】\n{body}".strip()
        if not text:
            continue
        metadata = {
            "source": parse_source(body),
            "anchor": title,
            "doc": path.name,
            "chunk_index": index,
            "content_type": "markdown",
            "category": path.parent.name,
        }
        if contextual_llm is not None:
            prefix = _make_contextual_prefix(text, f"{doc_context}\n本片段标题：{title}", contextual_llm)
            if prefix:
                text = f"【上下文】{prefix}\n{text}"
                metadata["contextual"] = True
        chunks.append(
            Chunk(
                chunk_id=f"{path.name}::{index}",
                text=text,
                metadata=metadata,
            )
        )
    return chunks


def _load_pdf(
    path: Path,
    max_chars: int,
    overlap_chars: int,
    preview_dir: Path | None,
    contextual_llm: Optional[Callable[[str, str], str]] = None,
) -> tuple[list[Chunk], bool]:
    try:
        from indexing.parser_router import route as parse_route
    except ImportError:
        print("[rag] indexing/parser_router 不可用，跳过 PDF:", path.name)
        return [], True

    decision = parse_route(str(path))
    print(f"[rag] {path.name}: {decision.reason}")
    parser = decision.parser_class()
    try:
        parsed = parser.parse(str(path))
    except Exception as exc:
        print(f"[rag] {path.name} 解析失败，跳过: {exc}")
        return [], True

    cleaned = clean_text(parsed.text)
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / f"{path.stem}.clean.txt").write_text(
            cleaned + "\n",
            encoding="utf-8",
        )

    chunks: list[Chunk] = []
    doc_context = f"文档：《{path.name}》，主题：膳食/营养/慢病忌口相关内容。"
    for index, raw_text in enumerate(
        chunk_by_paragraph(
            cleaned,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    ):
        text = raw_text
        metadata: dict[str, Any] = dict(parsed.metadata or {})
        if contextual_llm is not None:
            prefix = _make_contextual_prefix(text, doc_context, contextual_llm)
            if prefix:
                text = f"【上下文】{prefix}\n{text}"
                metadata["contextual"] = True
        metadata.update(
            {
                "source": metadata.get("source") or path.name,
                "anchor": f"{path.name}_p{index}",
                "doc": path.name,
                "chunk_index": index,
                "content_type": "pdf",
                "category": path.parent.name,
                "parser_used": metadata.get(
                    "parser_used", decision.parser_type.value
                ),
                "routing_reason": decision.reason,
            }
        )
        chunks.append(
            Chunk(
                chunk_id=f"{path.name}::{index}",
                text=text,
                metadata=metadata,
            )
        )
    return chunks, False


def load_chunks(
    corpus_dir: str | Path,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 120,
    preview_dir: str | Path | None = None,
    contextual_llm: Optional[Callable[[str, str], str]] = None,
) -> tuple[list[Chunk], int, int, int]:
    """读取语料并返回标准 Chunk 列表。"""

    corpus_path = resolve_project_path(corpus_dir)
    preview_path = (
        resolve_project_path(preview_dir) if preview_dir is not None else None
    )
    chunks: list[Chunk] = []

    # 仅允许 1~4 层（慢病食养 / DRI / 标签标识 / 特殊人群）进 RAG；
    # 0_总纲 / 5_食物底表 / 6_菜谱 / 9_工具 / README 等不进向量库。
    ALLOWED_LAYERS = ("0_", "1_", "2_", "3_", "4_")

    def _in_allowed_layer(p: Path) -> bool:
        return p.parent.name.startswith(ALLOWED_LAYERS)

    md_files = sorted(p for p in corpus_path.glob("**/*.md") if _in_allowed_layer(p))
    for path in md_files:
        chunks.extend(
            _load_markdown(path, max_chars, overlap_chars, preview_path, contextual_llm)
        )

    pdf_files = sorted(p for p in corpus_path.glob("**/*.pdf") if _in_allowed_layer(p))
    skipped_pdf_files = 0
    for path in pdf_files:
        pdf_chunks, skipped = _load_pdf(
            path,
            max_chars,
            overlap_chars,
            preview_path,
            contextual_llm,
        )
        chunks.extend(pdf_chunks)
        skipped_pdf_files += int(skipped)

    return chunks, len(md_files), len(pdf_files), skipped_pdf_files


def build_index(
    config: dict[str, Any] | None = None,
    *,
    preview: bool = False,
    contextual: bool = False,
    contextual_llm: Optional[Callable[[str, str], str]] = None,
) -> IndexBuildResult:
    """执行一次完整的离线建库。

    contextual: 是否为每个 chunk 生成上下文前缀（Contextual Retrieval）。
    contextual_llm: 注入式 LLM 可调用对象 llm(system, user) -> str；
        仅当 contextual=True 且提供了该对象时才生效，否则静默跳过。
    """

    if config is None:
        try:
            from configs import KB_CONFIG

            config = dict(KB_CONFIG)
        except Exception:
            config = {}

    corpus_dir = config.get("corpus_dir", "kb")
    kb_dir = config.get("kb_dir", "kb/chroma")
    collection_name = config.get("collection_name", DEFAULT_COLLECTION)
    embedding_backend = config.get("embedding_backend", "bge")
    max_chars = int(config.get("chunk_size", 1200))
    overlap_chars = int(config.get("chunk_overlap", 120))
    preview_dir = config.get("preview_dir", "resources/cleaned_preview")
    if contextual and contextual_llm is None:
        print("[rag] 已开启 contextual 但未提供 contextual_llm，将跳过上下文前缀生成。")

    effective_llm = contextual_llm if contextual else None
    chunks, md_count, pdf_count, skipped_pdf_count = load_chunks(
        corpus_dir,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        preview_dir=preview_dir if preview else None,
        contextual_llm=effective_llm,
    )
    store = ChromaStore(
        persist_dir=kb_dir,
        collection_name=collection_name,
        embedding_backend=embedding_backend,
    )
    store.rebuild(chunks)
    print(
        f"[rag] 已索引 {len(chunks)} 个 chunk "
        f"(md={md_count}, pdf={pdf_count}, skipped_pdf={skipped_pdf_count}, "
        f"embedding={embedding_backend}, collection={collection_name})"
    )
    if preview:
        print(f"[rag] 清洗预览已写入: {resolve_project_path(preview_dir)}")
    return IndexBuildResult(
        store=store,
        chunks=chunks,
        markdown_files=md_count,
        pdf_files=pdf_count,
        skipped_pdf_files=skipped_pdf_count,
    )


def self_test(#自检
    retriever_or_store,
    queries: list[str] | None = None,
) -> None:
    """用固定问题检查召回结果和来源 metadata。"""

    queries = queries or [
        "三高患者能吃燕麦吗",
        "高血压的人平时饮食要注意什么",
        "痛风能不能吃海鲜喝啤酒",
    ]
    print("\n[rag] 自检检索：")
    for query in queries:
        if hasattr(retriever_or_store, "search"):
            hits = retriever_or_store.search(query, n_results=2)
        else:
            result = retriever_or_store(query, n_results=2)
            hits = result.hits if isinstance(result, SearchResult) else []
        print(f"\n查询: {query}")
        for hit in hits:
            print(
                f"  - 距离={hit.distance:.3f} | 来源={hit.source} | "
                f"{hit.text[:80]}..."
            )
