"""跨会话菜品资产库。

只保存可复用的基础菜品资料和图片，不保存来源会话的健康结论、用户隐私或完整聊天上下文。
"""

import copy
import json
import re
import threading
import time
from pathlib import Path

from storage_utils import atomic_write_json


ASSETS_PATH = Path(__file__).resolve().parent / "data" / "dish_assets.json"
_LOCK = threading.Lock()


def _normalize_name(name: str) -> str:
    text = str(name or "").strip().lower()
    # 口味/版本后缀不应阻断基础菜品资产复用。
    text = re.sub(r"[（(].*?[）)]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _read_assets() -> dict:
    if not ASSETS_PATH.exists():
        return {}
    try:
        data = json.loads(ASSETS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_assets(data: dict) -> None:
    atomic_write_json(ASSETS_PATH, data)


def _recipe_asset(name: str, recipe: dict, image_url: str | None = None,
                  image_ai_generated: bool | None = None,
                  image_note: str | None = None) -> dict:
    asset = copy.deepcopy(recipe)
    asset["name"] = name
    if image_url is not None:
        asset["image_url"] = image_url
    if image_ai_generated is not None:
        asset["image_ai_generated"] = bool(image_ai_generated)
    if image_note is not None:
        asset["image_note"] = image_note
    return asset


def upsert_dish_asset(recipe: dict, session_id: str | None = None) -> bool:
    """保存一道菜的基础菜谱；没有图片时也先保存，后续补图会更新同一资产。"""
    if not isinstance(recipe, dict):
        return False
    name = str(recipe.get("name") or "").strip()
    key = _normalize_name(name)
    if not key:
        return False
    with _LOCK:
        assets = _read_assets()
        previous = assets.get(key) if isinstance(assets.get(key), dict) else {}
        merged = _recipe_asset(
            name,
            recipe,
            image_url=recipe.get("image_url") or previous.get("image_url"),
            image_ai_generated=(
                recipe.get("image_ai_generated")
                if recipe.get("image_url")
                else previous.get("image_ai_generated", False)
            ),
            image_note=recipe.get("image_note") or previous.get("image_note") or "",
        )
        assets[key] = {
            "name": name,
            "recipe": merged,
            "image_url": merged.get("image_url"),
            "image_ai_generated": bool(merged.get("image_ai_generated")),
            "image_note": merged.get("image_note") or "",
            "updated_at": time.time(),
            "source_session_id": session_id or previous.get("source_session_id"),
        }
        _write_assets(assets)
    return True


def find_dish_asset(name: str) -> dict | None:
    """按标准化菜名查找资产，优先精确匹配，再允许名称包含匹配。"""
    needle = _normalize_name(name)
    if not needle:
        return None
    with _LOCK:
        assets = _read_assets()
    exact = assets.get(needle)
    if isinstance(exact, dict):
        return copy.deepcopy(exact)
    candidates = []
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        if needle in key or key in needle:
            candidates.append((abs(len(key) - len(needle)), asset))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return copy.deepcopy(candidates[0][1])


def find_dish_asset_in_text(text: str) -> dict | None:
    """从自然语言中找已知菜品，优先最长匹配，避免短菜名抢先命中。"""
    normalized_text = _normalize_name(text)
    if not normalized_text:
        return None
    with _LOCK:
        assets = _read_assets()
    matches = [
        (len(key), asset)
        for key, asset in assets.items()
        if isinstance(asset, dict) and key and key in normalized_text
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return copy.deepcopy(matches[0][1])


def update_dish_asset_image(name: str, image_url: str, image_ai_generated: bool = False,
                            image_note: str = "") -> bool:
    """只更新已有资产的图片字段，避免异步补图覆盖菜谱正文。"""
    key = _normalize_name(name)
    if not key or not image_url:
        return False
    with _LOCK:
        assets = _read_assets()
        asset = assets.get(key)
        if not isinstance(asset, dict):
            return False
        asset["image_url"] = image_url
        asset["image_ai_generated"] = bool(image_ai_generated)
        asset["image_note"] = image_note or asset.get("image_note") or ""
        recipe = asset.get("recipe")
        if isinstance(recipe, dict):
            recipe["image_url"] = image_url
            recipe["image_ai_generated"] = bool(image_ai_generated)
            recipe["image_note"] = asset["image_note"]
        asset["updated_at"] = time.time()
        _write_assets(assets)
    return True
