# -*- coding: utf-8 -*-
"""T2 fridge inventory API: persist a simple ingredient list."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form

from api.routes.service_route import _split_inventory_text

router = APIRouter()
_FILE = Path(__file__).resolve().parents[2] / "data" / "fridge.json"
_PANTRY_LOG = Path(__file__).resolve().parents[2] / "data" / "pantry_log.json"


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
