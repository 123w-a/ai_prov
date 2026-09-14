"""用户长期饮食偏好 + 结构化健康画像接口（P1 家庭多成员版）。

preferences.txt：旧自由文本（保留兼容）
profile.json   ：结构化画像。v1=单成员平铺；v2={version:2, active_id, members[]}
                 读到 v1 自动迁移为单成员家庭档，写路径始终落 v2。
这是前端可管理的轻量长期记忆入口。
"""
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from storage_utils import atomic_write_json, atomic_write_text

router = APIRouter()

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_PREFS_PATH = _DATA_DIR / "preferences.txt"
_PROFILE_PATH = _DATA_DIR / "profile.json"

# 档案级互斥锁。atomic_write_json 只保证"不写出半截 JSON"，
# 保证不了「两个请求同时 读→改→写」——那会静默丢掉一方刚加的成员。
_PROFILE_LOCK = threading.RLock()


@contextmanager
def _family_lock():
    """把「读 → 改 → 写」整段包进档案锁，消除并发丢更新。"""
    with _PROFILE_LOCK:
        yield

class PreferencesPayload(BaseModel):
    preferences: str = Field(..., max_length=5000)

class BasicInfo(BaseModel):
    height_cm: Optional[float] = Field(default=None, ge=30, le=260)
    weight_kg: Optional[float] = Field(default=None, ge=2, le=500)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: str = Field(default="", pattern="^(|male|female|other)$")

class HealthProfilePayload(BaseModel):
    basic: BasicInfo = Field(default_factory=BasicInfo)
    conditions: List[str] = Field(default_factory=list, max_length=24)
    allergens: List[str] = Field(default_factory=list, max_length=24)
    goal: str = Field(default="", max_length=40)
    diet_style: str = Field(default="", max_length=40)
    dislikes: List[str] = Field(default_factory=list, max_length=60)
    taste_notes: List[str] = Field(default_factory=list, max_length=12)

class MemberPayload(BaseModel):
    """家庭成员 = 名字 + 一份结构化健康画像。"""
    name: str = Field(..., min_length=1, max_length=20)
    profile: HealthProfilePayload = Field(default_factory=HealthProfilePayload)

class ActiveMemberPayload(BaseModel):
    member_id: str = Field(..., min_length=1, max_length=40)


def _migrate(raw: dict) -> dict:
    """v1 平铺画像 / 损坏数据 → v2 家庭档（单成员『我的档案』）。"""
    if isinstance(raw, dict) and isinstance(raw.get("members"), list) and raw.get("members"):
        members = []
        for m in raw["members"]:
            if not isinstance(m, dict):
                continue
            members.append({
                "id": str(m.get("id") or f"m_{uuid.uuid4().hex[:8]}"),
                "name": str(m.get("name") or "成员").strip()[:20] or "成员",
                "profile": HealthProfilePayload(**(m.get("profile") or {})).model_dump(),
            })
        if members:
            active = str(raw.get("active_id") or members[0]["id"])
            if not any(m["id"] == active for m in members):
                active = members[0]["id"]
            return {"version": 2, "active_id": active, "members": members}
    legacy = HealthProfilePayload(**(raw if isinstance(raw, dict) else {})).model_dump()
    return {
        "version": 2,
        "active_id": "me",
        "members": [{"id": "me", "name": "我的档案", "profile": legacy}],
    }


def _read_family() -> dict:
    """读 profile.json 并保证返回 v2 家庭档；文件不存在返回 None。

    v1 平铺档读到即迁移落盘（一次迁移终身 v2）。"""
    if not _PROFILE_PATH.exists():
        return None
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"画像文件损坏：{exc}。已停止读写以免覆盖你的数据，"
                "请先备份 data/profile.json 再修复。"
            ),
        )
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(
            status_code=500,
            detail="画像文件结构异常：内容不是有效的 JSON 对象，护栏无法生效。",
        )
    if isinstance(raw.get("members"), list) and raw.get("members"):
        # 结构齐全的 v2 档直接返回；但成员全坏时不能静默降级成空档案。
        usable = [
            m for m in raw["members"]
            if isinstance(m, dict) and isinstance(m.get("profile"), dict)
        ]
        if not usable:
            raise HTTPException(
                status_code=500,
                detail="画像文件结构异常：没有任何可用的成员画像，护栏无法生效。",
            )
        return raw
    family = _migrate(raw)
    _write_family(family)
    return family


def _write_family(family: dict) -> None:
    atomic_write_json(_PROFILE_PATH, family)


def _find_member(family: dict, member_id: str):
    for m in family["members"]:
        if m["id"] == member_id:
            return m
    return None


@router.get("/preferences")
def get_preferences():
    text = ""
    if _PREFS_PATH.exists():
        text = _PREFS_PATH.read_text(encoding="utf-8")
    return {"code": 200, "messages": "偏好读取成功", "data": {"preferences": text}}

@router.put("/preferences")
def update_preferences(payload: PreferencesPayload):
    text = payload.preferences.replace("\r\n", "\n").replace("\r", "\n").strip()
    atomic_write_text(_PREFS_PATH, text + "\n" if text else "")
    return {"code": 200, "messages": "偏好已保存", "data": {"preferences": text}}

@router.get("/profile")
def get_profile():
    """读取家庭画像（v1 自动迁移）。返回 members 全量 + active_id。"""
    with _family_lock():
        family = _read_family()
        if family is None:
            default = _migrate({})
            return {
                "code": 200,
                "messages": "尚未建档",
                "data": {"exists": False, "family": default},
            }
        return {"code": 200, "messages": "画像读取成功", "data": {"exists": True, "family": family}}

@router.put("/profile")
def update_profile(payload: HealthProfilePayload):
    """兼容旧前端：整档写入 = 更新当前激活成员的画像。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        member = _find_member(family, family["active_id"]) or family["members"][0]
        member["profile"] = payload.model_dump()
        _write_family(family)
    return {"code": 200, "messages": "画像已保存", "data": {"exists": True, "family": family}}

@router.post("/profile/members")
def add_member(payload: MemberPayload):
    """新增家庭成员并设为激活（新建即切换，符合『给谁做饭就建谁』直觉）。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        if len(family["members"]) >= 8:
            raise HTTPException(status_code=400, detail="家庭成员最多 8 人")
        member = {
            "id": f"m_{uuid.uuid4().hex[:8]}",
            "name": payload.name.strip(),
            "profile": payload.profile.model_dump(),
        }
        family["members"].append(member)
        family["active_id"] = member["id"]
        _write_family(family)
    return {"code": 200, "messages": f"成员「{member['name']}」已建档并激活", "data": {"exists": True, "family": family}}

class DislikeAddPayload(BaseModel):
    item: str = Field(min_length=1, max_length=12)
    member_id: str | None = None   # 缺省 = 活跃成员


@router.post("/profile/dislikes/add")
def add_dislike(payload: DislikeAddPayload):
    """会话忌口沉淀：往指定（或缺省活跃）成员的 dislikes 里追加一项（去重）。

    与 update_member 的整成员覆盖不同，这里是原子 append——避免前端
    「先 GET 再 PUT」的合并竞态把并发修改冲掉。"""
    item = payload.item.strip()
    with _family_lock():
        family = _read_family() or _migrate({})
        member_id = payload.member_id or family.get("active_id")
        member = _find_member(family, member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        dislikes = list(member["profile"].get("dislikes") or [])
        if item not in dislikes:
            dislikes.append(item)
            member["profile"]["dislikes"] = dislikes[:12]   # 上限防膨胀
            _write_family(family)
            return {"code": 200, "messages": "已加入忌口", "data": {"added": True, "dislikes": dislikes}}
    return {"code": 200, "messages": "忌口已存在", "data": {"added": False, "dislikes": dislikes}}


class TasteNotePayload(BaseModel):
    text: str = Field(min_length=1, max_length=20)


@router.post("/profile/taste-note")
def add_taste_note(payload: TasteNotePayload):
    """口味偏好沉淀：往活跃成员 taste_notes 追加一条（去重），如「不吃辣」。"""
    text = payload.text.strip()
    with _family_lock():
        family = _read_family() or _migrate({})
        member = _find_member(family, family.get("active_id"))
        if member is None:
            raise HTTPException(status_code=404, detail="活跃成员不存在")
        notes = list(member["profile"].get("taste_notes") or [])
        if text not in notes:
            notes.append(text)
            member["profile"]["taste_notes"] = notes[:12]
            _write_family(family)
            return {"code": 200, "messages": "口味偏好已记录", "data": {"added": True, "taste_notes": notes}}
    return {"code": 200, "messages": "偏好已存在", "data": {"added": False, "taste_notes": notes}}


@router.get("/profile/taste-suggestion")
def taste_suggestion():
    """口味建议：近14天点踩里同口味≥2次 → 提示用户确认写入画像。"""
    from taste_store import suggest
    return {"code": 200, "data": {"suggestion": suggest(min_count=2)}}


@router.put("/profile/members/{member_id}")
def update_member(member_id: str, payload: MemberPayload):
    """更新成员姓名与画像（整成员覆盖）。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        member = _find_member(family, member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        member["name"] = payload.name.strip()
        member["profile"] = payload.profile.model_dump()
        _write_family(family)
    return {"code": 200, "messages": "成员已更新", "data": {"exists": True, "family": family}}

@router.delete("/profile/members/{member_id}")
def delete_member(member_id: str):
    """删除成员；至少保留一人，删除激活成员时自动切到剩余第一人。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        if len(family["members"]) <= 1:
            raise HTTPException(status_code=400, detail="至少保留一位家庭成员")
        member = _find_member(family, member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        family["members"] = [m for m in family["members"] if m["id"] != member_id]
        if family["active_id"] == member_id:
            family["active_id"] = family["members"][0]["id"]
        _write_family(family)
    try:
        from memory_candidates import clear_member_candidates

        clear_member_candidates(member_id, str(member.get("name") or ""))
    except Exception:
        pass
    return {"code": 200, "messages": f"成员「{member['name']}」已删除", "data": {"exists": True, "family": family}}


@router.delete("/profile/member/{member_id}")
def delete_member_singular(member_id: str):
    """单数路径别名：前端一键清除敏感画像时使用。"""
    return delete_member(member_id)

@router.put("/profile/active")
def switch_active(payload: ActiveMemberPayload):
    """切换激活成员：注入链只渲染激活成员画像。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        member = _find_member(family, payload.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        family["active_id"] = member["id"]
        _write_family(family)
    return {"code": 200, "messages": f"已切换到「{member['name']}」", "data": {"exists": True, "family": family}}


class ImportPayload(BaseModel):
    """分享文本导入体：members 与导出格式一致（可只填 name，其余走默认）。"""
    members: List[MemberPayload] = Field(..., min_length=1, max_length=8)
    meta: str = Field(default="", max_length=200)


@router.get("/profile/export")
def export_family():
    """导出全部成员为分享文本载体（复制给家人设备再导入）。"""
    with _family_lock():
        family = _read_family() or _migrate({})
    return {
        "code": 200,
        "messages": "导出成功",
        "data": {
            "export": {
                "app": "xiaoshan-profile",
                "version": 2,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "active_id": family["active_id"],
                "members": family["members"],
            }
        },
    }


@router.post("/profile/import")
def import_family(payload: ImportPayload):
    """导入分享成员（合并语义）：同名覆盖画像、新名追加，上限 8 人；激活指针不变。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        added = updated = 0
        for incoming in payload.members:
            name = incoming.name.strip()
            existing = next(
                (m for m in family["members"] if m["name"].strip() == name), None
            )
            if existing is not None:
                existing["profile"] = incoming.profile.model_dump()
                updated += 1
            else:
                if len(family["members"]) >= 8:
                    raise HTTPException(
                        status_code=400, detail=f"家庭成员最多 8 人，「{name}」未导入"
                    )
                family["members"].append({
                    "id": f"m_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "profile": incoming.profile.model_dump(),
                })
                added += 1
        _write_family(family)
    return {
        "code": 200,
        "messages": f"导入完成：新增 {added} 人，更新 {updated} 人",
        "data": {"exists": True, "family": family},
    }


@router.get("/preferences/candidates/pending")
def pending_candidates():
    """返回尚未确认的本地画像候选，不含任何远程同步逻辑。"""
    from memory_candidates import get_pending

    return {"code": 200, "data": {"candidates": get_pending()}}


@router.post("/preferences/candidates/{candidate_id}/confirm")
def confirm_candidate(candidate_id: str):
    from memory_candidates import confirm

    candidate = confirm(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在或成员已删除")
    return {"code": 200, "messages": "已加入长期饮食画像", "data": {"candidate": candidate}}


@router.post("/preferences/candidates/{candidate_id}/dismiss")
def dismiss_candidate(candidate_id: str):
    from memory_candidates import dismiss

    if not dismiss(candidate_id):
        raise HTTPException(status_code=404, detail="候选不存在")
    return {"code": 200, "messages": "已永久忽略该候选"}
