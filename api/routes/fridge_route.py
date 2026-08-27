# -*- coding: utf-8 -*-
"""T2 fridge inventory API: persist a simple ingredient list."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import base64

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.routes.service_route import _split_inventory_text

router = APIRouter()
_FILE = Path(__file__).resolve().parents[2] / "data" / "fridge.json"
_PANTRY_LOG = Path(__file__).resolve().parents[2] / "data" / "pantry_log.json"


def _parse_vision_json(text: str):
    """把视觉模型的回答解析成 [{name, quantity}]；解析失败返回 None。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    items = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        items.append({"name": name[:24], "quantity": str(entry.get("quantity") or "").strip()[:24]})
    return items


def _vision_extract_items(image_bytes: bytes, content_type: str) -> list[dict]:
    """调用视觉模型把冰箱/食材照片转成结构化食材清单。"""
    from langchain_core.messages import HumanMessage
    from model_name import get_langchain_llm

    llm = get_langchain_llm("gpt", temperature=0, max_tokens=600)
    image_url = "data:{};base64,{}".format(content_type, base64.b64encode(image_bytes).decode("ascii"))
    message = HumanMessage(content=[
        {"type": "text", "text": (
            "这是冰箱内部或食材的照片。请只识别照片中真实可见的食材，"
            "不要编造不存在的食材。输出严格的 JSON 数组，每个元素形如 "
            "{\"name\": \"鸡蛋\", \"quantity\": \"3个\"}。"
            "quantity 无法判断时留空字符串。只输出 JSON，不要解释。"
        )},
        {"type": "image_url", "image_url": {"url": image_url}},
    ])
    response = llm.invoke([message])
    content = response.content
    if isinstance(content, list):
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    items = _parse_vision_json(content)
    if items is None:
        raise ValueError("视觉模型输出无法解析为食材清单")
    return items


@router.post("/fridge/vision")
async def vision_fridge(image: UploadFile = File(...)):
    """拍照清点冰箱：返回 AI 识别的食材草稿，由用户确认后再写入库存。"""
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片内容为空")
    try:
        items = _vision_extract_items(image_bytes, image.content_type or "image/jpeg")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"视觉识别失败：{exc}")
    return {"items": items, "draft": True, "note": "AI 识别草稿，请确认或删改后写入"}


def _log_pantry_events(kind: str, items: list[str]) -> None:
    """Record purchase/consume events for weekly restock tips; failures never block saves."""
    if not items:
        return
    try:
        data = []
        if _PANTRY_LOG.exists():
            data = json.loads(_PANTRY_LOG.read_text(encoding="utf-8"))
        day = datetime.now().isoformat(timespec="seconds")[:10]
        data.extend({"date": day, "type": kind, "item": item} for item in items)
        _PANTRY_LOG.parent.mkdir(parents=True, exist_ok=True)
        _PANTRY_LOG.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


@router.get("/fridge")
def get_fridge():
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"items": []}


def _write_items(items: list[str]) -> dict:
    unique = list(dict.fromkeys(items))
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps({"items": unique}, ensure_ascii=False), encoding="utf-8")
    return {"items": unique, "saved": True}



@router.post("/fridge/set")
def set_fridge(items: str = Form("")):
    """Replace the fridge list; items dropped by the user count as consumed."""
    incoming = _split_inventory_text(items)
    previous = get_fridge().get("items", [])
    consumed = [item for item in previous if item not in incoming]
    _log_pantry_events("consume", consumed)
    return _write_items(incoming)


@router.post("/fridge/add")
def add_fridge(items: str = Form("")):
    """Append ingredients to the existing fridge list without duplicates."""
    existing = get_fridge().get("items", [])
    updated = existing + _split_inventory_text(items)
    purchased = [item for item in dict.fromkeys(updated) if item not in existing]
    _log_pantry_events("purchase", purchased)
    return _write_items(updated)
