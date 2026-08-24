"""附近餐厅接口：把旧 agent_tools.py 里的高德 POI 能力开放成独立 API。

无 AMAP_KEY / 断网 / 配额异常时回退 mock，保证演示不挂；
数据源会如实返回，前端不应把它包装成真实定位推荐。
"""
import importlib.util
from pathlib import Path

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


def _filter_and_sort(real, query: str, budget: int):
    if real is None:
        candidates = _MOCK_RESTAURANTS
        source = "mock"
    elif real:
        candidates = real
        source = "amap"
    else:
        candidates = _MOCK_RESTAURANTS
        source = "mock_empty"

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
    return source, candidates[:5]


@router.get("/nearby")
def nearby(
    query: str = "",
    city: str = "",
    district: str = "",
    budget: int = 50,
    location: str = "",
):
    if budget <= 0 or budget > 5000:
        raise HTTPException(status_code=400, detail="预算需在 1 到 5000 元之间")

    real = _amap_poi_search(city, district, query, budget, location)
    source, restaurants = _filter_and_sort(real, query, budget)
    return {
        "code": 200,
        "messages": "附近餐厅建议已返回",
        "data": {
            "source": source,
            "amap_configured": bool(AMAP_KEY),
            "restaurants": restaurants,
        },
    }