"""查询转换（RAG.md 第 5.4 讲）：改写 / 多查询 / HyDE。

用户问得口语化、绕、或带错别字时，直接拿原句去向量检索容易召回不准。
这里提供两种转换，把原查询改写成更适合检索的形式：

- ``hyde_transform``：让 LLM 先写一个「假设的答案段落」，连同原句一起检索
  （Hypothetical Document Embeddings，arXiv:2212.10496）。
- ``multi_query_transform``：让 LLM 生成 n 个改写变体，扩大召回面。

设计原则：检索层(rag)不依赖任何 LLM 框架。``llm`` 是一个注入式可调用对象，
签名为 ``llm(system: str, user: str) -> str``。默认不启用，由 Agent 层按需注入，
保证 rag 包可独立运行、离线可用。
"""

from __future__ import annotations

from typing import Callable, Sequence

_SYSTEM_HYPOTHETICAL = (
    "你是一个检索辅助器。给定用户关于饮食营养、慢病忌口、食材属性的提问，"
    "请写一段【假设的答案段落】（100字以内），包含可能用到的专业术语与依据要点，"
    "只输出段落本身，不要解释、不要加引号。"
)

_SYSTEM_REWRITE = "你是查询改写器。把用户问题改写成语义相同但表述不同的检索问句。"


def hyde_transform(query: str, llm: Callable[[str, str], str]) -> list[str]:
    """HyDE：返回 [原句, 假设答案段落]，两路一起召回。"""

    try:
        hypothetical = (llm(_SYSTEM_HYPOTHETICAL, query) or "").strip()
    except Exception:
        hypothetical = ""
    return [query, hypothetical] if hypothetical else [query]


def multi_query_transform(
    query: str, llm: Callable[[str, str], str], n: int = 3
) -> list[str]:
    """多查询：返回 [原句, 变体1, 变体2, ...]，最多 n 个变体。"""

    prompt = f"把下面这个问题改写成 {n} 个不同表述（每行一个，只输出问题本身）：\n{query}"
    try:
        out = llm(_SYSTEM_REWRITE, prompt) or ""
    except Exception:
        out = ""
    variants = [v.strip() for v in out.splitlines() if v.strip()]
    return [query, *variants][: n + 1]
