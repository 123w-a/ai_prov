"""Agent 工具兼容层。

项目根目录下原来的 ``agent_tools.py`` 保留为旧版本快照。
Python 导入 ``agent_tools`` 时优先进入这个包：普通工具仍复用旧实现，
知识库工具则改为调用解耦后的 ``rag.retriever``。
"""

from __future__ import annotations

import importlib.util
import re
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


# 健康化改造规则库（T1）：正则命中食谱元素 → 替/调建议 + 知识库检索关键词。
# 建议文案是通用营养原则；证据必须来自 RAG 命中，无命中时如实标注「通用原则」。
_REMIX_RULES = [
    (re.compile(r"冰糖|白糖|白砂糖|砂糖"), "精制糖换赤藓糖醇/罗汉果糖等天然代糖，等量甜度大幅减热量", "添加糖 限量"),
    (re.compile(r"油炸|深炸|宽油"), "改空气炸锅或少油煎，表面喷薄油即可上色，避免反复高温", "油炸 能量"),
    (re.compile(r"五花肉|肥膘|肥肉"), "换去皮鸡腿肉或瘦牛腩，饱和脂肪明显下降且蛋白不减", "饱和脂肪"),
    (re.compile(r"奶油|淡奶油"), "换希腊酸奶或低脂酸奶，口感近似、蛋白质更高脂肪更低", "乳制品"),
    (re.compile(r"酱油|生抽|老抽|蚝油"), "用量减半并换薄盐生抽，用花椒/八角/姜蒜等香料补足风味", "钠 限量"),
    (re.compile(r"米饭|面条|馒头"), "主食减三分之一并混入杂粮/魔芋米，降低升糖负荷增加膳食纤维", "全谷物 膳食纤维"),
]


@tool
def healthy_remix(recipe_text: str) -> str:
    """对用户给出的完整菜谱做「健康化改造」：替（食材替换）、调（做法调整），每条改造尽量附知识库出处。当用户粘贴食谱文本并希望吃得更健康时调用本工具。Args: recipe_text: 用户提供的原始菜谱全文。"""
    import json

    swaps = []
    for pat, advice, kw in _REMIX_RULES:
        m = pat.search(recipe_text)
        if not m:
            continue
        evidence = None
        try:
            res = search_knowledge_base(kw, n_results=1, transform=_build_transform())
            if not res.error and res.hits:
                h = res.hits[0]
                evidence = {
                    "source": h.source,
                    "section": getattr(h, "section", "") or "",
                    "excerpt": h.text[:120],
                }
        except Exception:
            evidence = None
        swaps.append({
            "type": "调" if ("改" in advice or "减" in advice) else "替",
            "match": m.group(0),
            "advice": advice,
            "evidence": evidence,
        })
    return json.dumps(
        {
            "found": bool(swaps),
            "swaps": swaps,
            "note": "以上为食养参考，不替代执业医师或营养师；无出处的条目为通用烹饪原则。",
        },
        ensure_ascii=False,
    )


@tool
def fridge_gap(recipe_name: str, inventory_text: str) -> str:
    """对照上门私厨标准菜谱库，检查用户已有食材相对目标菜谱还缺什么。当用户报出手头/冰箱食材并想知道做某道菜缺哪些料、或希望私厨补齐时调用。Args: recipe_name: 目标菜名（如 番茄炒蛋）；inventory_text: 已有食材的文字清单（逗号/空格分隔）。"""
    import json
    from api.routes.service_route import HOME_CHEF_RECIPES, _has_ingredient, _split_inventory_text

    key = next((k for k in HOME_CHEF_RECIPES if k in recipe_name or recipe_name in k), None)
    required = list(HOME_CHEF_RECIPES.get(key, []))
    owned = _split_inventory_text(inventory_text)
    missing = [item for item in required if not _has_ingredient(owned, item)]
    return json.dumps({
        "recipe_name": key or recipe_name,
        "recipe_matched": key is not None,
        "required_ingredients": required,
        "owned": owned,
        "missing": missing,
        "note": "missing 为空表示食材齐全；否则可在回复中建议用户补买，或说明上门私厨可携带这些原料。",
    }, ensure_ascii=False)


tools = [web_search, get_file, nearby_food, nutrition_kb_search, healthy_remix, fridge_gap]

__all__ = [
    "find_recipe_image",
    "fridge_gap",
    "get_file",
    "healthy_remix",
    "nearby_food",
    "nutrition_kb_search",
    "tools",
    "web_search",
]
