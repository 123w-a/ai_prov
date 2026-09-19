# memory_candidates.py：本地饮食画像候选记忆。
# 健康信息属于敏感个人信息：本文件和 data/profile.json 仅保存在本机，
# 不上传第三方；确认前只作为 pending 候选，用户可通过画像接口一键清除。

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from storage_utils import atomic_write_json


_DATA_DIR = Path(__file__).resolve().parent / "data"
_CANDIDATES_PATH = _DATA_DIR / "memory_candidates.json"

_TEMPORARY_WORDS = ("今天", "这次", "暂时", "先", "本顿", "这顿", "临时的")
_SOFT_WORDS = ("咬不动", "咀嚼困难", "软食", "硬的")
_ENERGY_WORDS = ("减重", "减肥", "控制体重")
_SPICY_WORDS = ("辣", "辛辣")
_CHRONIC_WORDS = ("高血压", "糖尿病", "高血脂", "高脂血症", "痛风", "高尿酸", "肾病", "肥胖", "孕期", "怀孕")
_ALLERGEN_WORDS = (
    "花生", "坚果", "核桃", "腰果", "杏仁", "虾", "蟹", "海鲜", "鱼", "蛋",
    "鸡蛋", "牛奶", "奶制品", "乳制品", "大豆", "豆制品", "小麦", "麸质",
    "芝麻",
)
_MEMBER_ALIASES = {
    "爷爷": ("爷爷", "祖父"),
    "奶奶": ("奶奶", "祖母"),
    "妈妈": ("妈妈", "母亲", "我妈"),
    "爸爸": ("爸爸", "父亲", "我爸"),
    "哥哥": ("哥哥", "我哥"),
    "弟弟": ("弟弟", "我弟"),
    "姐姐": ("姐姐", "我姐"),
    "妹妹": ("妹妹", "我妹"),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> list[dict]:
    if not _CANDIDATES_PATH.exists():
        return []
    try:
        raw = json.loads(_CANDIDATES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _save(items: list[dict]) -> None:
    atomic_write_json(_CANDIDATES_PATH, items)


def _member_rows(members: list) -> list[dict]:
    rows = []
    for item in members or []:
        if isinstance(item, dict):
            rows.append({
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "").strip(),
                "profile": item.get("profile") if isinstance(item.get("profile"), dict) else {},
            })
        else:
            rows.append({"id": "", "name": str(item).strip(), "profile": {}})
    return rows


def _match_member(user_text: str, members: list[dict]) -> tuple[str, str]:
    for member in members:
        name = member["name"]
        if name and name in user_text:
            return member["id"], name
    for canonical, aliases in _MEMBER_ALIASES.items():
        if any(alias in user_text for alias in aliases):
            for member in members:
                if member["name"] == canonical or any(alias in member["name"] for alias in aliases):
                    return member["id"], member["name"]
            # 明确提到家庭成员但档案里没有该成员时，不能回退到 active member。
            return "", ""
    if len(members) == 1:
        return members[0]["id"], members[0]["name"]
    return "", ""


def _candidate(
    member_id: str,
    member: str,
    dimension: str,
    value: str,
    source_text: str,
    severity: str,
    session_id: str = "",
    scope: str = "always",
) -> dict:
    return {
        "id": f"mc_{uuid.uuid4().hex[:10]}",
        "member": member,
        "member_id": member_id,
        "dimension": dimension,
        "value": value,
        "severity": severity,
        "scope": scope,
        "session_id": session_id,
        "source_text": source_text,
        "status": "pending",
        "created_at": _now(),
        "asked_at": "",
        "prompt_count": 0,
    }


def extract_candidates(user_text: str, members: list, session_id: str = "") -> list[dict]:
    """从持续性表达中提取候选；临时表达只作为 once，不生成长期候选。"""
    text = str(user_text or "").strip()
    if not text:
        return []
    rows = _member_rows(members)
    member_id, member = _match_member(text, rows)
    if any(word in text for word in _TEMPORARY_WORDS):
        return []

    found: list[tuple[str, str, str]] = []
    if any(word in text for word in _SOFT_WORDS):
        found.append(("texture", "软食", "soft"))

    for word in _CHRONIC_WORDS:
        if word in text and not any(item[1] == word for item in found):
            found.append(("chronic", word, "hard"))

    for word in _ENERGY_WORDS:
        if word in text:
            found.append(("energy", "减重", "soft"))
            break

    medical_restrict = any(
        marker in text
        for marker in ("医生要求", "医嘱", "医生说", "必须避免", "严格忌口", "治疗期间不能")
    )
    # 医嘱/治疗期间的限制必须进入硬约束，不得降级成口味偏好。
    if any(word in text for word in _SPICY_WORDS) and any(
        marker in text for marker in ("不能吃", "不吃", "忌口", "不要")
    ):
        found.append((
            "restrict" if medical_restrict else "preference",
            "不吃辣",
            "hard" if medical_restrict else "soft",
        ))

    for word in sorted(_ALLERGEN_WORDS, key=len, reverse=True):
        if word not in text or word == "辣":
            continue
        nearby = re.search(
            rf"(?:对\s*)?{re.escape(word)}[^，。；,\n]{{0,8}}过敏|"
            rf"(?:不能吃|不吃|忌口|不要)[^，。；,\n]{{0,4}}{re.escape(word)}",
            text,
        )
        if nearby:
            found.append(("allergen", word, "hard"))

    existing = _load()
    existing_keys = {
        (str(item.get("member_id") or ""), str(item.get("dimension") or ""), str(item.get("value") or ""))
        for item in existing
        if item.get("status") in ("pending", "confirmed")
    }
    for member_row in rows:
        profile = member_row.get("profile") or {}
        member_key = str(member_row.get("id") or "")
        for dimension, key in (
            ("allergen", "allergens"),
            ("chronic", "conditions"),
            ("restrict", "restricts"),
            ("preference", "taste_notes"),
            ("preference", "dislikes"),
        ):
            values = profile.get(key) or []
            if isinstance(values, str):
                values = [values]
            for value in values:
                text_value = str(value or "").strip()
                if text_value:
                    existing_keys.add((member_key, dimension, text_value))
    candidates = []
    for dimension, value, severity in found:
        key = (member_id, dimension, value)
        if key in existing_keys:
            continue
        candidates.append(
            _candidate(member_id, member, dimension, value, text, severity, session_id=session_id)
        )
        existing_keys.add(key)
    return candidates


def remember_candidates(candidates: list[dict]) -> list[dict]:
    """保存本轮提取的候选，后续必须由用户确认才会进入长期档案。"""
    if not candidates:
        return []
    items = _load()
    ids = {item.get("id") for item in items}
    for candidate in candidates:
        if candidate.get("id") not in ids:
            items.append(candidate)
    _save(items)
    return candidates


def should_ask(session_state: dict) -> bool:
    """同一会话最多主动提议 2 次，避免连续打扰。"""
    return int((session_state or {}).get("memory_asked_count") or 0) < 2


def get_pending(session_id: str | None = None) -> list[dict]:
    return [
        item
        for item in _load()
        if item.get("status") == "pending"
        and (session_id is None or str(item.get("session_id") or "") == str(session_id))
    ]


def get_pending_for_session(session_id: str) -> list[dict]:
    return get_pending(session_id)


def get_session_allergens(session_id: str | None) -> list[str]:
    """读取当前会话中仍处于 pending/once 的过敏原，供确定性硬护栏使用。"""
    if not session_id:
        return []
    values = []
    for item in _load():
        if item.get("status") not in ("pending", "once"):
            continue
        if str(item.get("dimension") or "") != "allergen":
            continue
        if str(item.get("session_id") or "") != str(session_id):
            continue
        value = str(item.get("value") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def mark_asked(candidate_ids: list[str]) -> list[dict]:
    """标记已向用户提议的候选，避免刷新或重复完成事件反复弹窗。"""
    wanted = {str(item) for item in candidate_ids if item}
    if not wanted:
        return []
    items = _load()
    marked = []
    for item in items:
        if str(item.get("id") or "") not in wanted or item.get("status") != "pending":
            continue
        item["asked_at"] = _now()
        item["prompt_count"] = int(item.get("prompt_count") or 0) + 1
        marked.append(item)
    if marked:
        _save(items)
    return marked


def remember_once(candidate_id: str) -> dict:
    items = _load()
    for item in items:
        if item.get("id") == candidate_id and item.get("status") == "pending":
            item["status"] = "once"
            item["scope"] = "once"
            _save(items)
            return item
    return {}


def confirm(candidate_id: str) -> dict:
    """用户确认后确定性写入 profile.json；LLM 不直接改 JSON。"""
    from api.routes.preferences_route import _migrate, _read_family, _write_family

    items = _load()
    candidate = next((item for item in items if item.get("id") == candidate_id), None)
    if candidate is None:
        return {}
    if candidate.get("status") == "confirmed":
        return candidate

    family = _read_family() or _migrate({})
    member_id = str(candidate.get("member_id") or "")
    member_name = str(candidate.get("member") or "")
    member = next(
        (
            item for item in family.get("members", [])
            if (member_id and item.get("id") == member_id)
            or (member_name and item.get("name") == member_name)
        ),
        None,
    )
    if member is None:
        return {}

    profile = member.setdefault("profile", {})
    dimension = candidate.get("dimension")
    value = str(candidate.get("value") or "")
    if dimension == "allergen":
        values = list(profile.get("allergens") or [])
        if value not in values:
            values.append(value)
        profile["allergens"] = values
    elif dimension == "chronic":
        values = list(profile.get("conditions") or [])
        if value not in values:
            values.append(value)
        profile["conditions"] = values
    elif dimension == "energy":
        profile["goal"] = value
    elif dimension == "restrict":
        values = list(profile.get("restricts") or [])
        if value not in values:
            values.append(value)
        profile["restricts"] = values
    else:
        values = list(profile.get("taste_notes") or [])
        if value not in values:
            values.append(value)
        profile["taste_notes"] = values

    _write_family(family)
    candidate["status"] = "confirmed"
    _save(items)
    return candidate


def dismiss(candidate_id: str) -> bool:
    items = _load()
    for item in items:
        if item.get("id") == candidate_id:
            item["status"] = "dismissed"
            _save(items)
            return True
    return False


def clear_member_candidates(member_id: str, member_name: str = "") -> int:
    """删除成员时同步清理候选，避免残留敏感健康信息。"""
    items = _load()
    kept = [
        item for item in items
        if not (
            (member_id and item.get("member_id") == member_id)
            or (member_name and item.get("member") == member_name)
        )
    ]
    removed = len(items) - len(kept)
    if removed:
        _save(kept)
    return removed


def render_pending_constraints(session_id: str | None = None) -> str:
    """把 pending 候选作为本轮严格约束注入；它不会写长期档案。"""
    items = _load()
    pending = [
        item for item in items
        if item.get("status") in ("pending", "once")
        and (
            session_id is None
            or str(item.get("session_id") or "") == str(session_id)
        )
    ]
    if not pending:
        return ""
    labels = []
    for item in pending:
        member = str(item.get("member") or "家庭成员")
        value = str(item.get("value") or "")
        if value:
            qualifier = "硬约束" if item.get("severity") == "hard" else "本轮偏好"
            labels.append(f"{member}：{value}（{qualifier}）")
    if not labels:
        return ""
    return (
        "【待确认画像约束（本轮立即遵守，未经确认不写长期档案）】\n- "
        + "\n- ".join(dict.fromkeys(labels))
    )
