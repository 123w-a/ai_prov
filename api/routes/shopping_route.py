"""T2-P1 采购清单：把想吃的若干道菜合并成一张去重购物清单。
GET /api/shopping/list?dishes=番茄炒蛋,红烧肉&inventory=鸡蛋,葱
- 复用上门私厨标准菜谱库的必备食材；inventory 里有就不列。
- 调味料单独归组，主料归一组，方便按区采购。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from api.routes.service_route import HOME_CHEF_RECIPES, _has_ingredient, _split_inventory_text

router = APIRouter()
_SEASONINGS = {"盐", "糖", "食用油", "生抽", "老抽", "醋", "料酒", "蚝油"}


@router.get("/shopping/list")
def shopping_list(dishes: str = "", inventory: str = ""):
    wanted = [d.strip() for d in dishes.split(",") if d.strip()]
    owned = _split_inventory_text(inventory)
    matched, unknown, main_items, seasoning_items = [], [], [], []
    seen = set()
    for d in wanted:
        key = next((k for k in HOME_CHEF_RECIPES if k in d or d in k), None)
        if not key:
            unknown.append(d)
            continue
        matched.append(key)
        for item in HOME_CHEF_RECIPES[key]:
            if item in seen or _has_ingredient(owned, item):
                continue
            seen.add(item)
            (seasoning_items if item in _SEASONINGS else main_items).append(item)

    # LLM 兜底：菜谱库没有的菜，让模型列常见必备食材，同样并入清单。
    # 失败静默跳过——unknown_dishes 里仍如实可见，不假装覆盖。
    if unknown:
        try:
            from agent_chains import structure_llm
            from langchain_core.messages import HumanMessage
            prompt = (
                "列出这些家常菜的常见必备食材（不要做法步骤）。每行一道菜，格式严格为："
                "菜名:食材1、食材2\n只输出这几行，不要任何解释。菜：" + "、".join(unknown)
            )
            resp = structure_llm.invoke([HumanMessage(content=prompt)])
            import re as _re
            for line in str(resp.content).splitlines():
                if ":" not in line and "：" not in line:
                    continue
                name, _, body = _re.split(r"[:：]", line, maxsplit=1)
                name = name.strip().split("（")[0]
                key2 = next((k for k in unknown if k in name or name in k), None)
                if not key2:
                    continue
                unknown.remove(key2)
                matched.append(key2 + "（模型补录）")
                for item in [x.strip()[:12] for x in _re.split(r"[、,，]", body) if x.strip()]:
                    if item in seen or _has_ingredient(owned, item):
                        continue
                    seen.add(item)
                    (seasoning_items if item in _SEASONINGS else main_items).append(item)
        except Exception:
            pass
    return json.loads(json.dumps({
        "matched_dishes": matched,
        "unknown_dishes": unknown,
        "main": main_items,
        "seasoning": seasoning_items,
        "note": "已自动扣除你说有的食材；调味料默认家里常备，不需要的可手动划掉。",
    }, ensure_ascii=False))