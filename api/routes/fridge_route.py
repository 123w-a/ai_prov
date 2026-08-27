# -*- coding: utf-8 -*-
"""T2 fridge inventory API: persist a simple ingredient list."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Form

from api.routes.service_route import _split_inventory_text

router = APIRouter()
_FILE = Path(__file__).resolve().parents[2] / "data" / "fridge.json"


@router.get("/fridge")
def get_fridge():
    try:
        if _FILE.exists():
            return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"items": []}


@router.post("/fridge/set")
def set_fridge(items: str = Form("")):
    """Update fridge list from comma-separated ingredient string."""
    tokens = _split_inventory_text(items)
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps({"items": tokens}, ensure_ascii=False), encoding="utf-8")
    return {"items": tokens, "saved": True}
