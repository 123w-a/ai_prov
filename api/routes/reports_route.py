"""T2-P0 饮食记录 + 周报：每次决策产出菜品时自动记账，/api/reports/weekly 聚合近7天。
数据文件 data/meals.json（JSON 数组），字段尽量少、诚实可查。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

from api.routes.fridge_route import _PANTRY_LOG, get_fridge

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
        "light_trends": _light_trends(recent, since),
        "guardrail_triggers": sum(m.get("guardrails", 0) for m in recent),
        "range": [since.date().isoformat(), datetime.now().date().isoformat()],
        "next_week_shopping": _next_week_shopping(dishes.most_common(5)),
        "recommendations": _weekly_recommendations(dishes.most_common(5), _light_trends(recent, since))
                             + _pantry_restock_tips(_read_pantry_log(), set(get_fridge().get("items", []))),
    }


def _light_trends(recent: list[dict], since: datetime) -> dict[str, str]:
    """Compare risk-light ratios in the latest three days with the prior four.

    A risk light is yellow or red. Trends require at least two observations in
    each window so sparse history is shown honestly instead of over-interpreted.
    """
    split = since + timedelta(days=4)
    windows: dict[str, list[str]] = {"previous": [], "latest": []}
    for meal in recent:
        bucket = "latest" if datetime.fromisoformat(meal["ts"]) >= split else "previous"
        windows[bucket].extend(meal.get("lights", []))

    labels = {light.rsplit(":", 1)[0] for values in windows.values() for light in values if ":" in light}
    trends: dict[str, str] = {}
    for label in labels:
        previous = [light.rsplit(":", 1)[1] for light in windows["previous"] if light.startswith(label + ":")]
        latest = [light.rsplit(":", 1)[1] for light in windows["latest"] if light.startswith(label + ":")]
        if len(previous) < 2 or len(latest) < 2:
            trends[label] = "insufficient"
            continue
        previous_risk = sum(level in {"yellow", "red"} for level in previous) / len(previous)
        latest_risk = sum(level in {"yellow", "red"} for level in latest) / len(latest)
        if latest_risk < previous_risk:
            trends[label] = "improving"
        elif latest_risk > previous_risk:
            trends[label] = "worsening"
        else:
            trends[label] = "stable"
    return trends


_TREND_TIPS = {
    "钠": "近几餐钠风险抬头：主动少放半勺盐，避开咸菜、腌肉这类高钠配料。",
    "糖": "近几餐糖风险抬头：少碰含糖饮料和甜汤，甜味优先交给水果本味。",
    "脂肪": "近几餐脂肪风险抬头：换蒸煮做法，炒菜油再收半勺，肥肉先放一放。",
}


def _weekly_recommendations(top_dishes: list, light_trends: dict[str, str]) -> list[str]:
    """Turn observed weekly evidence into a few honest suggestions.

    Every suggestion must trace back to a real signal (trend or dish mix);
    sparse data gets no directional advice.
    """
    tips: list[str] = []
    for label, trend in light_trends.items():
        if trend == "worsening" and label in _TREND_TIPS:
            tips.append(_TREND_TIPS[label])
    if top_dishes and not any(t == "insufficient" for t in light_trends.values()):
        names = "、".join(d for d, _ in top_dishes[:2])
        tips.append(f"这周常吃{names}；维持当前口味的同时，下一周可以换一种蛋白质或深色蔬菜做搭配。")
    return tips


def _read_pantry_log() -> list[dict]:
    try:
        return json.loads(_PANTRY_LOG.read_text(encoding="utf-8")) if _PANTRY_LOG.exists() else []
    except Exception:
        return []


def _pantry_restock_tips(log: list[dict], owned: set[str], today: datetime | None = None) -> list[str]:
    """Suggest restocking frequently repurchased items that are currently missing."""
    today = today or datetime.now()
    since = (today - timedelta(days=14)).date().isoformat()
    counts = Counter(e["item"] for e in log if e.get("type") == "purchase" and e.get("date", "") >= since)
    tips: list[str] = []
    for item, count in counts.most_common():
        if len(tips) >= 2:
            break
        if item in owned or count < 2:
            continue
        tips.append(f"「{item}」近两周已补货 {count} 次、冰箱暂时没有——下次采购记得带上。")
    return tips


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