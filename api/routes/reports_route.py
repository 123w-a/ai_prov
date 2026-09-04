"""T2-P0 饮食记录 + 周报：每次决策产出菜品时自动记账，/api/reports/weekly 聚合近7天。
数据文件 data/meals.json（JSON 数组），字段尽量少、诚实可查。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.routes.fridge_route import _PANTRY_LOG, get_fridge

router = APIRouter()
_MEALS = Path(__file__).resolve().parents[2] / "data" / "meals.json"
_FEEDBACK_LOG = Path(__file__).resolve().parents[2] / "data" / "feedback.json"
_SUMMARY_CACHE = Path(__file__).resolve().parents[2] / "data" / "weekly_summary.json"


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
        "recommendations": _weekly_recommendations(dishes.most_common(5), _light_trends(recent, since))
                             + _pantry_restock_tips(_read_pantry_log(), set(get_fridge().get("items", [])))
                             + _feedback_tips(_read_feedback_log()),
        "feedback_summary": _feedback_summary(_read_feedback_log()),
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


def _read_feedback_log() -> list[dict]:
    try:
        return json.loads(_FEEDBACK_LOG.read_text(encoding="utf-8")) if _FEEDBACK_LOG.exists() else []
    except Exception:
        return []


_FEEDBACK_TAG_TIPS = {"偏咸": "主动再减盐", "偏油": "烹饪用油再收一收", "偏淡": "调味可稍补一点"}


def _feedback_tips(entries: list[dict], today: datetime | None = None) -> list[str]:
    """把最近一周的真实用餐反馈提炼成建议；没有反馈就一条不说。"""
    today = today or datetime.now()
    since = (today - timedelta(days=7)).date().isoformat()
    recent = [e for e in entries if str(e.get("ts", ""))[:10] >= since]
    tips: list[str] = []
    tag_counts = Counter(t for e in recent for t in (e.get("tags") or []))
    for tag, tip in _FEEDBACK_TAG_TIPS.items():
        if tag_counts.get(tag, 0) >= 2:
            tips.append(f"本周 {tag_counts[tag]} 餐反馈『{tag}』——{tip}。")
        if len(tips) >= 2:
            break
    rated = [e for e in recent if isinstance(e.get("rating"), (int, float))]
    if len(tips) < 2 and len(rated) >= 2:
        avg = sum(e["rating"] for e in rated) / len(rated)
        if avg <= 2:
            tips.append(f"本周 {len(rated)} 餐平均评分仅 {avg:.1f} 分——建议换换菜式或调整做法。")
    return tips


def _feedback_summary(entries: list[dict], today: datetime | None = None) -> dict:
    today = today or datetime.now()
    since = (today - timedelta(days=7)).date().isoformat()
    recent = [e for e in entries if str(e.get("ts", ""))[:10] >= since]
    tags = Counter(t for e in recent for t in (e.get("tags") or []))
    return {"count": len(recent), "tags": dict(tags)}


@router.post("/reports/feedback")
def save_feedback(payload: dict):
    """记录一次用餐反馈：{dish, rating(1-5), tags?, comment?}。"""
    dish = str(payload.get("dish") or "").strip()[:40]
    rating = payload.get("rating")
    if not dish:
        raise HTTPException(status_code=400, detail="dish 不能为空")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="rating 必须是 1-5 的整数")
    tags = [str(t).strip()[:8] for t in (payload.get("tags") or []) if str(t).strip()][:4]
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "dish": dish,
        "rating": rating,
        "tags": tags,
        "comment": str(payload.get("comment") or "").strip()[:120],
    }
    data = _read_feedback_log()
    data.append(entry)
    try:
        _FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        _FEEDBACK_LOG.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"反馈写入失败：{exc}")
    return {"saved": True, "count": len(data)}


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


def _recent_meals() -> list[dict]:
    """近 7 天记账记录（weekly 与 summary 共用）。"""
    try:
        data = json.loads(_MEALS.read_text(encoding="utf-8")) if _MEALS.exists() else []
    except Exception:
        data = []
    since = datetime.now() - timedelta(days=7)
    return [m for m in data if datetime.fromisoformat(m["ts"]) >= since]


def _active_conditions() -> list[str]:
    """活跃成员的健康条件标签（读不到就空表，不阻塞小结）。"""
    try:
        from api.routes.preferences_route import _read_family
        family = _read_family()
        if not family:
            return []
        active = next(
            (m for m in family["members"] if m["id"] == family.get("active_id")), None
        )
        return list((active or {}).get("profile", {}).get("conditions") or [])
    except Exception:
        return []


def _weekly_evidence() -> dict | None:
    """汇总本周证据包（供指纹与 LLM prompt）；无数据返回 None。"""
    recent = _recent_meals()
    if not recent:
        return None
    dishes = Counter(m["dish"] for m in recent if m.get("dish"))
    lights = Counter(l for m in recent for l in m.get("lights", []))
    trends = _light_trends(recent, datetime.now() - timedelta(days=7))
    tags = Counter(
        t for e in _read_feedback_log()
        for t in (e.get("tags") or [])
        if str(e.get("ts", ""))[:10] >= (datetime.now() - timedelta(days=7)).date().isoformat()
    )
    try:
        from feedback_store import recent_down_dishes
        downs = recent_down_dishes(days=7, limit=3)
    except Exception:
        downs = []
    return {
        "meals": len(recent),
        "top_dishes": dishes.most_common(5),
        "lights": dict(lights),
        "trends": trends,
        "feedback_tags": dict(tags),
        "down_dishes": downs,
        "conditions": _active_conditions(),
    }


_SUMMARY_PROMPT = (
    "你是饮食健康小结助手。根据以下本周真实数据写 3-5 句中文小结，第二人称『你』。"
    "要求：具体点名菜名与红绿灯/趋势；有数据才下结论，缺数据就说样本还少；"
    "严禁编造数据之外的事实；语气自然不堆砌；直接输出小结正文。\n\n本周数据：\n{evidence}"
)


@router.get("/reports/weekly-summary")
def weekly_summary(refresh: bool = False):
    """本周 AI 自然语言小结：数据指纹缓存，数据不变不重复调用 LLM。

    refresh=1 跳过缓存强制重生成（用户点『重新生成』）。"""
    evidence = _weekly_evidence()
    if evidence is None:
        return {"ai_summary": None, "reason": "no_data"}
    fingerprint = hashlib.md5(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if not refresh:
        try:
            cache = json.loads(_SUMMARY_CACHE.read_text(encoding="utf-8"))
            if cache.get("fingerprint") == fingerprint and cache.get("ai_summary"):
                return {"ai_summary": cache["ai_summary"], "cached": True}
        except Exception:
            pass
    try:
        from model_name import get_langchain_llm, resolve_provider
        llm = get_langchain_llm(resolve_provider(), temperature=0.3, max_tokens=500)
        lines = [
            f"餐数：{evidence['meals']}",
            f"常吃：{('、'.join(d for d, _ in evidence['top_dishes'])) or '无'}",
            f"红绿灯计数：{evidence['lights']}",
            f"趋势：{evidence['trends']}",
            f"用餐反馈标签：{evidence['feedback_tags'] or '无'}",
            f"近期被踩菜品：{('、'.join(evidence['down_dishes'])) or '无'}",
            f"健康条件标签：{('、'.join(evidence['conditions'])) or '无'}",
        ]
        response = llm.invoke(_SUMMARY_PROMPT.format(evidence=chr(10).join(lines)))
        summary = str(response.content).strip()
    except Exception as exc:
        return {"ai_summary": None, "reason": f"llm_failed: {exc}"[:160]}
    if not summary:
        return {"ai_summary": None, "reason": "empty"}
    try:
        _SUMMARY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SUMMARY_CACHE.write_text(
            json.dumps(
                {"fingerprint": fingerprint, "ai_summary": summary,
                 "ts": datetime.now().isoformat(timespec="seconds")},
                ensure_ascii=False, indent=1,
            ), encoding="utf-8",
        )
    except Exception:
        pass
    return {"ai_summary": summary, "cached": False}
