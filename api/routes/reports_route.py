"""T2-P0 饮食记录 + 周报：每次决策产出菜品时自动记账，/api/reports/weekly 聚合近7天。
数据文件 data/meals.json（JSON 数组），字段尽量少、诚实可查。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
_MEALS = Path(__file__).resolve().parents[2] / "data" / "meals.json"


def record_meal(session_id: str, answer: dict) -> None:
    """从 ChefAnswer 提取最小字段追加入库；解析失败静默跳过（不阻塞聊天）。"""
    try:
        recipes = answer.get("recipes") or []
        if not recipes:
            return
        r0 = recipes[0]
        meal = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": session_id,
            "dish": str(r0.get("name", "") or "")[:40],
            "lights": [f"{l.get('label','')}:{l.get('level','')}" for l in (answer.get("health_lights") or [])],
            "guardrails": len(answer.get("guardrails") or []),
        }
        data = []
        if _MEALS.exists():
            data = json.loads(_MEALS.read_text(encoding="utf-8"))
        data.append(meal)
        _MEALS.parent.mkdir(parents=True, exist_ok=True)
        _MEALS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


@router.get("/reports/weekly")
def weekly_report():
    """近 7 天聚合：餐数、菜品 Top、红绿灯计数、护栏触发次数。空数据返回诚实提示。"""
    try:
        data = json.loads(_MEALS.read_text(encoding="utf-8")) if _MEALS.exists() else []
    except Exception:
        data = []
    since = datetime.now() - timedelta(days=7)
    recent = [m for m in data if datetime.fromisoformat(m["ts"]) >= since]
    if not recent:
        return {"has_data": False, "message": "近 7 天还没有饮食决策记录。去聊一道菜吧，吃完自动记一笔。"}
    dishes = Counter(m["dish"] for m in recent if m.get("dish"))
    lights = Counter(l for m in recent for l in m.get("lights", []))
    return {
        "has_data": True,
        "meals": len(recent),
        "top_dishes": dishes.most_common(5),
        "lights": dict(lights),
        "guardrail_triggers": sum(m.get("guardrails", 0) for m in recent),
        "range": [since.date().isoformat(), datetime.now().date().isoformat()],
        "next_week_shopping": _next_week_shopping(dishes.most_common(5)),
    }


def _next_week_shopping(top_dishes: list) -> list[str]:
    """T2-P1.5：把本周常吃菜对应的主料合并去重，作为下周购物参考清单。"""
    try:
        from api.routes.service_route import HOME_CHEF_RECIPES
    except Exception:
        return []
    items: list[str] = []
    seen: set[str] = set()
    for dish, _n in top_dishes:
        key = next((k for k in HOME_CHEF_RECIPES if k in dish or dish in k), None)
        if not key:
            continue
        for ing in HOME_CHEF_RECIPES[key]:
            if ing not in seen:
                seen.add(ing)
                items.append(ing)
    return items