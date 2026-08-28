"""PILOT ONLY — 隔离副本，不动 rag/query_transform.py 现网资产。

DY-C 自举产出：multi_query 改写提示词加系统级指令（繁体归一＋口语→领域术语）。
转正与否由用户依据 before/after 红绿报告裁决。
"""
from typing import Callable

_MULTI_QUERY_SYSTEM = (
    "你是营养健康知识库的检索查询改写器。规则："
    "①繁体字一律转换为简体；"
    "②口语与民间说法扩展为规范表达，如「管用」→「有效」「帮助」「补铁」；"
    "③只改写表达，不引入新的疾病名或结论；"
    "④输出 {n} 个简体中文检索式，每行一个，只输出问题本身。"
)

def multi_query_variant(query: str, llm: Callable[[str, str], str], n: int = 3) -> list[str]:
    try:
        text = (llm(_MULTI_QUERY_SYSTEM.format(n=n), query) or "").strip()
    except Exception:
        return [query]
    lines = [l.strip(" \t-·0123456789.") for l in text.splitlines() if l.strip()]
    return [query] + [l for l in lines if l and l != query][:n]
