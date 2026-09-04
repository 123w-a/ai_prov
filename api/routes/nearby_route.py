"""附近餐厅接口：把旧 agent_tools.py 里的高德 POI 能力开放成独立 API。

无 AMAP_KEY / 断网 / 配额异常时回退 mock，保证演示不挂；
数据源会如实返回，前端不应把它包装成真实定位推荐。
"""
import importlib.util
from pathlib import Path

import requests

from fastapi import APIRouter, HTTPException

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_PATH = _PROJECT_ROOT / "agent_tools.py"
_spec = importlib.util.spec_from_file_location("_legacy_agent_tools", _LEGACY_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载旧工具模块: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_legacy)

AMAP_KEY = _legacy.AMAP_KEY
_MOCK_RESTAURANTS = _legacy._MOCK_RESTAURANTS
_amap_poi_search = _legacy._amap_poi_search


def _filter_and_sort(real, query: str, budget: int, page: int = 1):
    if real is None:
        candidates = _MOCK_RESTAURANTS
        source = "mock"
        warning = "网络异常，展示模拟参考餐厅"
    elif real:
        candidates = real
        source = "amap"
        warning = ""
    else:
        candidates = _MOCK_RESTAURANTS
        source = "mock_empty"
        warning = "高德未返回结果，展示模拟参考餐厅"

    if budget > 0:
        if source == "amap":
            priced = [c for c in candidates if c.get("avg_price") and c["avg_price"] <= budget]
            candidates = priced if priced else candidates
        else:
            under = [c for c in candidates if c.get("avg_price", 0) <= budget]
            candidates = under if under else _MOCK_RESTAURANTS[:3]

    query = (query or "").strip()
    if query:
        candidates.sort(
            key=lambda c: (
                query not in c.get("name", "")
                and query not in c.get("cuisine", "")
                and query not in c.get("address", "")
            )
        )
    if source == "amap":
        return source, candidates[:5], warning
    # mock 分页：按 page 轮转 3 家，模拟“换一批”而不重复当前列表
    offset = ((max(1, int(page or 1)) - 1) * 3) % len(candidates)
    candidates = candidates[offset:] + candidates[:offset]
    return source, candidates[:3], warning


@router.get("/nearby")
def nearby(
    query: str = "",
    city: str = "",
    district: str = "",
    budget: int = 50,
    location: str = "",
    radius: int = 1500,
    page: int = 1,
):
    if budget < 0 or budget > 5000:
        raise HTTPException(status_code=400, detail="预算需在 0 到 5000 元之间，0 表示不限")
    if radius <= 0 or radius > 10000:
        raise HTTPException(status_code=400, detail="radius 需在 1 到 10000 米之间")
    if page < 1 or page > 20:
        raise HTTPException(status_code=400, detail="page 需在 1 到 20 之间")

    real = _amap_poi_search(city, district, query, budget, location, page=page, radius=radius)
    source, restaurants, warning = _filter_and_sort(real, query, budget, page=page)
    return {
        "code": 200,
        "messages": "附近餐厅建议已返回",
        "data": {
            "source": source,
            "amap_configured": bool(AMAP_KEY),
            "restaurants": restaurants,
            "warning": warning,
        },
    }


@router.get("/location/resolve")
def resolve_location(location: str = ""):
    lng_lat = (location or "").strip()
    if not lng_lat:
        return {
            "code": 200,
            "messages": "定位解析失败，请手动选择城市",
            "data": {
                "resolved": False,
                "location": "",
                "city": "",
                "district": "",
                "label": "定位解析失败，请手动选择城市",
                "warning": "定位解析失败，请手动选择城市",
            },
        }

    if not AMAP_KEY:
        return {
            "code": 200,
            "messages": "定位解析失败，请手动选择城市",
            "data": {
                "resolved": False,
                "location": lng_lat,
                "city": "",
                "district": "",
                "label": "定位解析失败，请手动选择城市",
                "warning": "定位解析失败，请手动选择城市",
            },
        }

    try:
        resp = requests.get(
            "https://restapi.amap.com/v3/geocode/regeo",
            params={
                "key": AMAP_KEY,
                "location": lng_lat,
                "radius": 1000,
                "extensions": "base",
                "batch": "false",
                "roadlevel": 0,
            },
            timeout=5,
        )
        data = resp.json()
    except Exception:
        data = {}

    if data.get("status") != "1":
        return {
            "code": 200,
            "messages": "定位解析失败，请手动选择城市",
            "data": {
                "resolved": False,
                "location": lng_lat,
                "city": "",
                "district": "",
                "label": "定位解析失败，请手动选择城市",
                "warning": "定位解析失败，请手动选择城市",
            },
        }

    comp = (data.get("regeocode") or {}).get("addressComponent") or {}
    city = str(comp.get("city") or comp.get("province") or "").strip()
    district = str(comp.get("district") or comp.get("township") or "").strip()
    label = "已定位"
    if city and district:
        label = f"{city} · {district}"
    elif city:
        label = city
    elif district:
        label = district
    else:
        label = "定位解析失败，请手动选择城市"

    return {
        "code": 200,
        "messages": "定位已就绪",
        "data": {
            "resolved": True,
            "location": lng_lat,
            "city": city,
            "district": district,
            "label": label,
            "warning": "",
        },
    }
