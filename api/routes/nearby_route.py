"""附近餐厅接口：把旧 agent_tools.py 里的高德 POI 能力开放成独立 API。

`/nearby` 返回带 mock 兜底的餐厅列表；`/nearby/map` 只返回真实高德坐标对应的
地图视图模型，降级时 marker 为空并如实给出 warning。两者共用同一条检索与
过敏原过滤链路，但不改变 `/nearby` 的既有契约。
"""
import importlib.util
import re
from pathlib import Path

import requests

from fastapi import APIRouter, HTTPException

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_PATH = _PROJECT_ROOT / "agent_tools" / "legacy.py"
_spec = importlib.util.spec_from_file_location("_legacy_agent_tools", _LEGACY_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载旧工具模块: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_legacy)

AMAP_KEY = _legacy.AMAP_KEY
_MOCK_RESTAURANTS = _legacy._MOCK_RESTAURANTS
_amap_poi_search = _legacy._amap_poi_search

_AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
# 高德是国内 API，直连即可；显式禁用环境代理，理由见 agent_tools/legacy.py 的 AMAP_NO_PROXY。
_AMAP_NO_PROXY = {"http": None, "https": None}
_COORDINATE_PAIR_RE = re.compile(
    r"(-?\d{1,3}(?:\.\d+)?)\s*[,，]\s*(-?\d{1,3}(?:\.\d+)?)"
)

# 高德 Web 服务错误码 →（状态描述，用户能做的动作）。
# 表取自官方《错误码说明》 https://lbs.amap.com/api/webservice/guide/tools/info （2026-10-12 对照）。
# 命中就补一句人话；**没命中也不吞**——原始的 info/infocode 一律原样带进 warning。
_AMAP_CODE_HINT = {
    "10001": ("Key 不正确或已过期", "请到高德控制台确认这个 Key 还在、没过期，再把新的 AMAP_KEY 写回 .env 并重启后端。"),
    "10002": ("这个 Key 没有权限使用该服务，或请求路径写错", "请在高德控制台给该 Key 开通「Web服务」，并确认已申请「逆地理编码」。"),
    "10003": ("当日访问量已超限，被系统自动封停", "等当天 0:00 自动解封，或在控制台提升配额。"),
    "10004": ("单位时间（1 分钟）访问过于频繁", "降低调用频率，下一分钟自动解封。"),
    "10005": ("调用方 IP 不在该 Key 的 IP 白名单内", "请在控制台把这台机器的出口 IP 加进白名单，或清空白名单。"),
    "10006": ("该 Key 绑定的域名无效", "请在控制台重新设置绑定域名。"),
    "10007": ("数字签名未通过验证", "该 Key 开了数字签名：按官方算法补签名，或关掉数字签名。"),
    "10008": ("MD5 安全码未通过验证", "请核对该 Key 绑定的 SHA1/包名是否与调用端一致。"),
    "10009": (
        "请求 Key 与绑定的平台不符（例如拿 JS API / Android 的 Key 调 Web 服务接口）",
        "请在高德控制台新建一个「Web服务」类型的 Key（或给现有 Key 添加「Web服务」平台），"
        "把它填进 .env 的 AMAP_KEY 并重启后端。",
    ),
    "10010": ("单 IP 访问超限，已被封停且不会自动恢复", "需要在高德控制台提交工单解封，并建议设置 IP 白名单。"),
    "10011": ("该服务不支持 https 请求", "请改用 http，或提交工单申请 https 支持。"),
    "10012": ("权限不足，该服务请求被拒绝", "请在控制台核对该 Key 是否包含「逆地理编码」服务权限。"),
    "10013": ("Key 已被删除", "请在高德控制台重新创建 Key 并更新 .env。"),
    "10019": ("某个服务的总 QPS 超限", "降低并发，或申请提升 QPS。"),
    "10020": ("该 Key 调用该服务的 QPS 超限", "降低并发；限流阈值内的请求仍会正常返回。"),
    "10021": ("账号调用该服务的 QPS 超限", "降低并发；限流阈值内的请求仍会正常返回。"),
    "10026": ("账号处于被封禁状态", "请登录高德控制台提交工单申诉。"),
    "20000": ("请求参数非法", "请确认 location 是「经度,纬度」且在中国大陆范围内。"),
    "20001": ("缺少必填参数", "请确认请求带上了 location。"),
    "20002": ("请求协议非法（该接口只支持 GET）", "请用 GET 调用本接口。"),
    "20011": ("坐标在海外，而这个 Key 没有海外地图权限", "境外坐标解析不出中国行政区名属正常；大陆坐标请检查经纬度有没有写反。"),
    "20800": ("坐标不在中国大陆陆地范围内", "请确认坐标在中国大陆范围内（境外坐标高德不返回中国行政区名）。"),
    "40000": ("所购买服务的余额耗尽", "请到高德控制台充值，或换一个可用的 Key。"),
    "40002": ("所购买的服务已到期", "请到高德控制台续费，或换一个可用的 Key。"),
}


def _scrub(text: str) -> str:
    """任何要进响应或日志的文本都先过这里：绝不把 Key 内容写出去。"""
    key = AMAP_KEY or ""
    return text.replace(key, "***") if key and key in text else text


def _valid_coordinate(lng, lat) -> bool:
    """只接受可落到地图上的经纬度；(0,0) 是常见的未定位占位值，不当作真实位置。"""
    try:
        lng_value = float(lng)
        lat_value = float(lat)
    except (TypeError, ValueError):
        return False
    if lng_value == 0 and lat_value == 0:
        return False
    return -180 <= lng_value <= 180 and -90 <= lat_value <= 90


def _parse_location(location: str) -> dict | None:
    """从 `location` 中提取一对经纬度（兼容「[当前位置：经度,纬度]」这类整串）。"""
    match = _COORDINATE_PAIR_RE.search(str(location or ""))
    if not match:
        return None
    lng, lat = float(match.group(1)), float(match.group(2))
    if not _valid_coordinate(lng, lat):
        return None
    return {"lng": round(lng, 6), "lat": round(lat, 6)}


def _map_markers(restaurants: list[dict] | None) -> list[dict]:
    """把检索结果投影成地图 marker；没有有效坐标的餐厅一律不进地图。"""
    markers = []
    for item in restaurants or []:
        lng = item.get("lng")
        lat = item.get("lat")
        if not _valid_coordinate(lng, lat):
            continue
        markers.append(
            {
                "name": str(item.get("name") or ""),
                "cuisine": str(item.get("cuisine") or ""),
                "avg_price": item.get("avg_price") or 0,
                "distance_km": item.get("distance_km"),
                "lng": round(float(lng), 6),
                "lat": round(float(lat), 6),
                "address": str(item.get("address") or ""),
                "guardrail": str(item.get("guardrail") or ""),
            }
        )
    return markers


def _markers_center(markers: list[dict]) -> dict | None:
    """未定位时用本批真实餐厅的外接矩形中心，避免写死任何城市坐标。"""
    if not markers:
        return None
    lngs = [item["lng"] for item in markers]
    lats = [item["lat"] for item in markers]
    return {
        "lng": round((min(lngs) + max(lngs)) / 2, 6),
        "lat": round((min(lats) + max(lats)) / 2, 6),
    }


def _with_warning(warning: str, extra: str) -> str:
    warning = (warning or "").strip()
    extra = (extra or "").strip()
    if not extra:
        return warning
    if not warning:
        return extra
    return f"{warning.rstrip('。')}。{extra}"


def _validate_search_params(budget: int, radius: int, page: int) -> None:
    if budget < 0 or budget > 5000:
        raise HTTPException(status_code=400, detail="预算需在 0 到 5000 元之间，0 表示不限")
    if radius <= 0 or radius > 10000:
        raise HTTPException(status_code=400, detail="radius 需在 1 到 10000 米之间")
    if page < 1 or page > 20:
        raise HTTPException(status_code=400, detail="page 需在 1 到 20 之间")


def _failure(location: str, why: str, action: str = "", detail: str = "") -> dict:
    """解析失败的统一信封。

    信封字段与成功时完全一致，`resolved` 仍表示「是否解析出了位置名」。
    `label` 放短原因；`messages` / `warning` 放「原因（含原始 info/infocode 或异常）+ 能做什么」——
    前端直接显示 warning 就是真话，不需要自己再编一句固定文案。
    """
    why = _scrub(why)
    detail = _scrub(detail)
    reason = f"{why}（{detail}）" if detail else why
    text = f"{reason}。{action}" if action else reason
    return {
        "code": 200,
        "messages": text,
        "data": {
            "resolved": False,
            "location": location,
            "city": "",
            "district": "",
            "label": why,
            "warning": text,
        },
    }


def _amap_failure_warning(diag: dict | None) -> str:
    """高德检索失败时说出**真实原因**，而不是笼统的「网络异常」。

    本节顶部那张 _AMAP_CODE_HINT 原本就承诺「没命中也不吞——原始的 info/infocode
    一律原样带进 warning」，但在此之前 legacy 的 _amap_poi_search 把
    「Key 平台不匹配(10009)」「配额耗尽」「断网」统统压成同一个 `return None`，
    于是这里只能一律写「网络异常」，那张表**永远走不到**。
    2026-10-14 实测：真实原因是 infocode 10009 / USERKEY_PLAT_NOMATCH
    （拿 JS API 类型的 Key 去调 Web 服务接口），界面却报「网络异常」——
    用户会去查网络，而这个错永远修不好。
    """
    if not diag:
        return "网络异常，展示模拟参考餐厅"
    if diag.get("info") == "no_key":
        return "后端没有配置高德 Key（AMAP_KEY 为空），展示模拟参考餐厅"
    exc = diag.get("exception")
    if exc:
        return _scrub(f"连接高德失败（{exc}），展示模拟参考餐厅")
    code = str(diag.get("infocode") or "").strip()
    info = str(diag.get("info") or "").strip()
    if not code and not info:
        return "网络异常，展示模拟参考餐厅"
    desc, action = _AMAP_CODE_HINT.get(code, ("", ""))
    parts = [
        f"高德拒绝了这次检索：{info or '未知错误'}" + (f"（infocode {code}）" if code else "")
    ]
    if desc:
        parts.append(f"含义：{desc.rstrip('。')}")
    if action:
        # 对照表里的动作句自带句号，拼接时先剥掉，否则会出现「。。」
        parts.append(f"要修的是：{action.rstrip('。')}")
    elif code:
        parts.append(f"这个错误码不在本机对照表里，请按官方说明核对：infocode {code}")
    return _scrub("。".join(parts) + "。已展示模拟参考餐厅")


def _filter_and_sort(real, query: str, budget: int, page: int = 1, diag: dict | None = None):
    if real is None:
        candidates = _MOCK_RESTAURANTS
        source = "mock"
        warning = _amap_failure_warning(diag)
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
    _validate_search_params(budget, radius, page)

    # diag 收集高德这次为什么失败（断网 / Key 平台不符 / 配额……）：
    # 返回 None 这一种结果本身分不出原因，而原因决定了用户该去改什么。
    diag: dict = {}
    real = _amap_poi_search(
        city, district, query, budget, location, page=page, radius=radius, diag=diag
    )
    source, restaurants, warning = _filter_and_sort(real, query, budget, page=page, diag=diag)
    before_filter = len(restaurants)
    restaurants, allergen_hits = _legacy._filter_restaurants_by_allergens(restaurants)
    data = {
        "source": source,
        "amap_configured": bool(AMAP_KEY),
        "restaurants": restaurants,
        "warning": warning,
    }
    if allergen_hits:
        note = _legacy._allergen_filter_note(allergen_hits)
        data["allergen_filter"] = {
            "applied": True,
            "removed": before_filter - len(restaurants),
            "message": note,
        }
    return {
        "code": 200,
        "messages": "附近餐厅建议已返回",
        "data": data,
    }


@router.get("/nearby/map")
def nearby_map(
    query: str = "",
    city: str = "",
    district: str = "",
    budget: int = 50,
    location: str = "",
    radius: int = 1500,
    page: int = 1,
):
    """附近餐厅地图视图模型（增量接口，不改变 `/nearby` 的契约）。

    - `location` 为 GCJ-02 的「经度,纬度」（与浏览器定位转换后的口径一致）；
      传了就同时作为检索圆心、`origin` 与地图 `center`。
    - 没传定位时，用本批真实餐厅坐标的外接矩形中心作为 `center`。
    - 只有 `source=amap` 且带有效坐标的餐厅才生成 marker；mock 和失败降级
      一律返回空 marker 与真实 warning，绝不伪造坐标。
    - 只返回 `amap_configured` 布尔值，绝不把 AMAP_KEY 写进响应。
    """
    _validate_search_params(budget, radius, page)

    origin = _parse_location(location)
    search_location = f"{origin['lng']},{origin['lat']}" if origin else ""
    warning = ""
    if str(location or "").strip() and origin is None:
        warning = "收到的定位坐标无法解析，已改为按关键词/城市检索"

    diag: dict = {}
    real = _amap_poi_search(
        city,
        district,
        query,
        budget,
        search_location,
        page=page,
        radius=radius,
        diag=diag,
    )
    source, restaurants, search_warning = _filter_and_sort(
        real, query, budget, page=page, diag=diag
    )
    before_filter = len(restaurants)
    restaurants, allergen_hits = _legacy._filter_restaurants_by_allergens(restaurants)

    markers = _map_markers(restaurants) if source == "amap" else []
    warning = _with_warning(warning, search_warning)
    if source == "amap" and restaurants and not markers:
        warning = _with_warning(
            warning,
            "高德返回了结果，但没有可用的经纬度，地图不展示标记",
        )

    data = {
        "source": source,
        "amap_configured": bool(AMAP_KEY),
        "coordinate_system": "gcj02",
        "center": origin or _markers_center(markers),
        "origin": origin,
        "markers": markers,
        "warning": warning,
    }
    if allergen_hits:
        data["allergen_filter"] = {
            "applied": True,
            "removed": before_filter - len(restaurants),
            "message": _legacy._allergen_filter_note(allergen_hits),
        }
    return {
        "code": 200,
        "messages": "附近餐厅地图数据已返回",
        "data": data,
    }


@router.get("/location/resolve")
def resolve_location(location: str = ""):
    lng_lat = (location or "").strip()
    if not lng_lat:
        return _failure(
            "",
            "没有收到坐标（location 为空），无法解析位置名",
            "请点「用我的位置」重试；不想授权定位就按关键词查，附近餐厅照样能出。",
        )

    if not AMAP_KEY:
        return _failure(
            lng_lat,
            "后端没有配置高德 Key（AMAP_KEY 为空），无法解析位置名",
            "请在 .env 里填一个「Web服务」类型的 AMAP_KEY 并重启后端；在那之前仍可按关键词查。",
        )

    resp = None
    raw_text = ""
    try:
        resp = requests.get(
            _AMAP_REGEO_URL,
            params={
                "key": AMAP_KEY,
                "location": lng_lat,
                "radius": 1000,
                "extensions": "base",
                "batch": "false",
                "roadlevel": 0,
            },
            timeout=5,
            proxies=_AMAP_NO_PROXY,
        )
        raw_text = getattr(resp, "text", "") or ""
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 —— 这里必须把异常说清楚，不许再变成无声的 {}
        bits = [f"{type(exc).__name__}: {exc}"]
        status_code = getattr(resp, "status_code", None)
        if status_code is not None:
            bits.append(f"HTTP {status_code}")
        snippet = " ".join(raw_text.split())[:200]
        if snippet:
            bits.append(f"响应体片段={snippet}")
        return _failure(
            lng_lat,
            "调用高德逆地理编码失败，没能拿到位置名",
            "请先确认这台机器能不能访问 restapi.amap.com，然后重试；也可以先按关键词查。",
            "；".join(bits),
        )

    if not isinstance(data, dict):
        data = {}

    status = str(data.get("status") or "").strip()
    info = str(data.get("info") or "").strip()
    infocode = str(data.get("infocode") or "").strip()
    if status != "1":
        hint = _AMAP_CODE_HINT.get(infocode)
        if hint:
            why = f"高德逆地理编码被拒——{hint[0]}"
            action = hint[1]
        else:
            why = f"高德逆地理编码被拒（status={status or '空'}）"
            action = "请把下面的 info/infocode 拿到高德控制台，核对该 Key 的平台与「逆地理编码」权限。"
        return _failure(lng_lat, why, action, f"info={info or '空'}, infocode={infocode or '空'}")

    regeocode = data.get("regeocode")
    if not isinstance(regeocode, dict):
        regeocode = {}
    comp = regeocode.get("addressComponent")
    if not isinstance(comp, dict):
        comp = {}

    city = str(comp.get("city") or comp.get("province") or "").strip()
    district = str(comp.get("district") or comp.get("township") or "").strip()
    if not city and not district:
        formatted = str(regeocode.get("formatted_address") or "").strip()
        return _failure(
            lng_lat,
            "高德这次返回了成功，但 addressComponent 里没有城市和区县，拿不到位置名",
            "可以继续按坐标查附近餐厅；如果这个坐标确实在中国大陆，请把这条 warning 反馈给维护者。",
            _scrub(
                f"info={info or '空'}, infocode={infocode or '空'}；"
                f"formatted_address={formatted or '空'}；addressComponent={comp!r}"
            )[:300],
        )

    label = "已定位"
    if city and district:
        label = f"{city} · {district}"
    elif city:
        label = city
    else:
        label = district

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
