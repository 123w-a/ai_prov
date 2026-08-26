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
    return json.loads(json.dumps({
        "matched_dishes": matched,
        "unknown_dishes": unknown,
        "main": main_items,
        "seasoning": seasoning_items,
        "note": "已自动扣除你说有的食材；调味料默认家里常备，不需要的可手动划掉。",
    }, ensure_ascii=False))