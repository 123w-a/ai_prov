"""Agent 工具兼容层。

项目根目录下原来的 ``agent_tools.py`` 保留为旧版本快照。
Python 导入 ``agent_tools`` 时优先进入这个包：普通工具仍复用旧实现，
知识库工具则改为调用解耦后的 ``rag.retriever``。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from langchain_core.tools import tool

from rag.query_transform import hyde_transform, multi_query_transform
from rag.retriever import search as search_knowledge_base


# 查询转换（RAG.md 第 5.4 讲）：由 Agent 层注入真实 LLM 后启用，默认关闭（离线安全）。
# 注入式签名固定为 llm(system, user) -> str，与 query_transform 模块对齐。
_query_transform_llm = None
_query_transform_mode = "multi"
_query_transform_n = 3


def set_query_transform_llm(llm, mode: str = "multi", n: int = 3) -> None:#把传进来的适配器存进全局变量 _query_transform_llm，并记下 mode="multi"
    """Agent 层在启动时调用本函数注入真实 LLM，开启查询转换。

    mode="multi" -> 多查询改写；mode="hyde" -> HyDE 假设答案段落。
    未调用本函数时 nutrition_kb_search 退化为「直接拿原句检索」。
    """

    global _query_transform_llm, _query_transform_mode, _query_transform_n
    _query_transform_llm = llm
    _query_transform_mode = mode
    _query_transform_n = n


def _build_transform():#返回一个可以直接用的涵数造出一台打印机lambda
    if _query_transform_llm is None:
        return None
    if _query_transform_mode == "hyde":
        return lambda q: hyde_transform(q, _query_transform_llm)
    return lambda q: multi_query_transform(q, _query_transform_llm, n=_query_transform_n)


def _load_legacy_tools():#简化导入包要取的名字，即可用旧导入也可以用新导入法
    legacy_path = Path(__file__).resolve().parent.parent / "agent_tools.py"
    spec = importlib.util.spec_from_file_location(
        "_legacy_agent_tools",
        legacy_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载旧工具模块: {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_legacy = _load_legacy_tools()
#为了外部兼容这个使用方法
web_search = _legacy.web_search
nearby_food = _legacy.nearby_food
get_file = _legacy.get_file
find_recipe_image = _legacy.find_recipe_image


@tool
def nutrition_kb_search(query: str, n_results: int = 3, source_filter: str = "") -> str:
    """检索本地营养/膳食知识库，并返回可溯源的命中片段。

    Args:
        query: 用户关于营养/慢病忌口/食材属性的问题。
        n_results: 返回几条命中（默认 3）。
        source_filter: 可选，只返回该来源（如《中国居民膳食指南2022》）的命中；
                       为空则不过滤。对应 RAG.md 第 5.1 讲元数据过滤。
    """

    import json

    flt = {"source": source_filter} if source_filter else None
    result = search_knowledge_base(#原始数据+想返回几条+画好圈子中找是我一开始打好的标签，这个涵数是包了一层适配器的LLM这里是语义转换后再检索
        query, n_results=n_results, filter=flt, transform=_build_transform()#即转换又查询检索
    )
    if result.error:
        return json.dumps(
            {"found": False, "error": result.error, "hits": []},
            ensure_ascii=False,
        )
    if not result.hits:
        return json.dumps({"found": False, "hits": []}, ensure_ascii=False)

    hits = [
        {
            "source": hit.source,
            "section": hit.section,
            "distance": round(hit.distance, 3),
            "excerpt": hit.text[:240],
            "metadata": hit.metadata,
        }
        for hit in result.hits
    ]
    return json.dumps({"found": True, "hits": hits}, ensure_ascii=False)


tools = [web_search, get_file, nearby_food, nutrition_kb_search]

__all__ = [
    "find_recipe_image",
    "get_file",
    "nearby_food",
    "nutrition_kb_search",
    "tools",
    "web_search",
]
