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

from storage.utils import atomic_write_json, atomic_write_text

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
    # 2026-10-11 新增（全部可空，不改既有字段语义）。都挂在 basic 下：这两个数
    # 与身高/体重/年龄同类，是"身体的基础数据"，档案页那一块「基础数据」读的
    # 就是 profile.basic。
    #   · gestational_weeks 孕周 —— 只能由用户自己录入；NULL = 没登记。
    #     上限 45 是**防呆**（医学上孕周不会更多），不是把值往上凑。
    #   · body_fat_rate 体脂率（%）—— 用户录入或体脂秤同步。**当前算不出来**：
    #     后端没有可用的体脂推算模型，禁止用 BMI 之类的估算值填这个字段。
    #     上界 70 同样只是防呆，不是正常值域。
    gestational_weeks: Optional[int] = Field(default=None, ge=0, le=45)
    body_fat_rate: Optional[float] = Field(default=None, ge=1, le=70)

class HealthProfilePayload(BaseModel):
    basic: BasicInfo = Field(default_factory=BasicInfo)
    conditions: List[str] = Field(default_factory=list, max_length=24)
    allergens: List[str] = Field(default_factory=list, max_length=24)
    restricts: List[str] = Field(default_factory=list, max_length=24)
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


def _member_public(member: dict, meals: list[dict] | None = None) -> dict:
    """成员档案的**只读投影**：补齐新增字段（旧档缺 → null）+ 注入算出来的营养进度。

    为什么返回新对象而不就地改：`nutrition_progress` 是算出来的、**不落盘**。
    就地改 `_read_family()` 返回的那个 dict 的话，下一次 `_write_family` 会把它
    写进 profile.json，之后 v2 快速路径又会把它当既有字段原样读回来——等于拿一份
    过期的进度当数据。所以投影必须是拷贝。

    「旧档案缺字段 → null」也在这里兜：v2 快速路径是**原样返回**磁盘内容的
    （见 `_read_family`），老成员的 basic 里根本没有这两个键，不补就是 undefined
    而不是 null；而"不迁移、不猜值"是 2026-10-11 定的口径，所以只在响应里补
    null，不写回磁盘、也不替用户填任何默认数值。

    营养进度算不出来（导入失败/异常）时同样给 null——null 的语义就是"后端还不知道"，
    前端据此显示「待补」；不能让一个附加字段把整份档案读崩。

    `meals` 由 `_family_payload` 读一次传进来：8 位成员不要读 8 遍盘，更重要的是
    同一份响应里所有成员必须**看到同一份记账快照**（否则两个人可能跨在一次写入
    的两侧，页面上两个进度就不是同一时刻的）。
    """
    profile = dict(member.get("profile") or {})
    basic = dict(profile.get("basic") or {})
    basic.setdefault("gestational_weeks", None)
    basic.setdefault("body_fat_rate", None)
    profile["basic"] = basic
    try:
        from api.routes.reports_route import member_nutrition_progress

        profile["nutrition_progress"] = member_nutrition_progress(member, meals=meals)
    except Exception:
        profile["nutrition_progress"] = None
    out = dict(member)
    out["profile"] = profile
    return out


def _shared_dislikes(members: list[dict]) -> list[str]:
    """家户级忌口 = **每个人都忌**的那几样（交集），不是并集（2026-10-14 新增）。

    为什么后端要出这个字段：这个判断原先在两处前端各写了一遍，而且**口径不一致**——
    `next/data/household.ts` 算的是交集（对的），`next/data/firstScreen.ts` 算的是并集
    （错的）。当前真实档案两人恰好都忌芹菜，所以两者结果相同、错的那个一直没露出来；
    一旦只有一个人忌香菜，首屏就会写「共同忌口 香菜」——把个人忌口说成全家的。
    口径只能有一个，所以判定搬回后端，前端两处都读同一个值。

    **不是"家户级忌口"这个新概念**：后端没有、也不新增任何"全家必须忌"的独立配置，
    这个值完全由各成员自己的 dislikes 推出来，改了成员就跟着变。

    单人家庭返回 []（一个人的忌口是他自己的，不是全家的）；顺序跟随第一位成员，
    不用 sorted——忌口顺序是用户自己填的，排序会让同一个值在不同次请求里换位置。
    """
    if len(members) < 2:
        return []
    sets: list[set[str]] = []
    for m in members:
        raw = (m.get("profile") or {}).get("dislikes") or []
        sets.append({str(x).strip() for x in raw if str(x).strip()})
    if not sets:
        return []
    first = [str(x).strip() for x in ((members[0].get("profile") or {}).get("dislikes") or [])]
    return [d for d in dict.fromkeys(first) if d and all(d in s for s in sets)]


def _family_payload(family: dict) -> dict:
    """响应里的家庭档：成员逐个过 `_member_public`，其余字段（version/active_id）原样，
    再补一个算出来的 `shared_dislikes`（不落盘，同 `nutrition_progress` 的处理）。"""
    try:
        from api.routes.reports_route import read_meals

        meals: list[dict] | None = read_meals()
    except Exception:
        meals = None  # 读不动记账 → 交回 _member_public 的兜底（进度给 null）
    members = family.get("members") or []
    return {
        **family,
        "members": [_member_public(m, meals=meals) for m in members],
        # 只加在响应里、不写回 profile.json：它是推导值，落盘就会变成过期数据
        # （与 nutrition_progress 同一条理由，见 _member_public 的说明）。
        "shared_dislikes": _shared_dislikes(members),
    }


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
    """读取家庭画像（v1 自动迁移）。返回 members 全量 + active_id。

    成员画像经 `_member_public` 投影：新增字段（孕周/体脂率/营养进度）齐全，
    旧档案缺失的键一律为 null。"""
    with _family_lock():
        family = _read_family()
        if family is None:
            default = _migrate({})
            return {
                "code": 200,
                "messages": "尚未建档",
                "data": {"exists": False, "family": _family_payload(default)},
            }
        return {
            "code": 200,
            "messages": "画像读取成功",
            "data": {"exists": True, "family": _family_payload(family)},
        }

@router.put("/profile")
def update_profile(payload: HealthProfilePayload):
    """兼容旧前端：整档写入 = 更新当前激活成员的画像。"""
    with _family_lock():
        family = _read_family() or _migrate({})
        member = _find_member(family, family["active_id"]) or family["members"][0]
        member["profile"] = payload.model_dump()
        _write_family(family)
    return {"code": 200, "messages": "画像已保存", "data": {"exists": True, "family": _family_payload(family)}}

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
    return {"code": 200, "messages": f"成员「{member['name']}」已建档并激活", "data": {"exists": True, "family": _family_payload(family)}}

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
    from storage.taste import suggest
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
    return {"code": 200, "messages": "成员已更新", "data": {"exists": True, "family": _family_payload(family)}}

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
        from storage.memory_candidates import clear_member_candidates

        clear_member_candidates(member_id, str(member.get("name") or ""))
    except Exception:
        pass
    return {"code": 200, "messages": f"成员「{member['name']}」已删除", "data": {"exists": True, "family": _family_payload(family)}}


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
    return {"code": 200, "messages": f"已切换到「{member['name']}」", "data": {"exists": True, "family": _family_payload(family)}}


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
        "data": {"exists": True, "family": _family_payload(family)},
    }


@router.get("/preferences/candidates/pending")
def pending_candidates(session_id: Optional[str] = None):
    """返回尚未确认的本地画像候选，不含任何远程同步逻辑。"""
    from storage.memory_candidates import get_pending, mark_asked

    candidates = get_pending(session_id)
    # 每次只把尚未主动提议过的候选交给前端；服务端也限制批量，避免历史积压一次弹出。
    fresh = [item for item in candidates if int(item.get("prompt_count") or 0) == 0][:2]
    mark_asked([str(item.get("id") or "") for item in fresh])
    # 只兼容旧版本真正没有成员上下文的候选；明确提到但未建档的成员
    # 会保留候选里的名字，不能被当前激活成员覆盖。
    with _family_lock():
        family = _read_family() or _migrate({})
    active = _find_member(family, str(family.get("active_id") or ""))
    if active is not None:
        for item in fresh:
            if not item.get("member_id") and not item.get("member"):
                item["member_id"] = active["id"]
                item["member"] = active["name"]
            item["is_new_member"] = bool(item.get("member") and not item.get("member_id"))
    return {"code": 200, "data": {"candidates": fresh}}


@router.post("/preferences/candidates/{candidate_id}/confirm")
def confirm_candidate(candidate_id: str):
    from storage.memory_candidates import confirm

    candidate = confirm(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在或成员已删除")
    return {"code": 200, "messages": "已加入长期饮食画像", "data": {"candidate": candidate}}


@router.post("/preferences/candidates/{candidate_id}/dismiss")
def dismiss_candidate(candidate_id: str):
    from storage.memory_candidates import dismiss

    if not dismiss(candidate_id):
        raise HTTPException(status_code=404, detail="候选不存在")
    return {"code": 200, "messages": "已永久忽略该候选"}


@router.post("/preferences/candidates/{candidate_id}/once")
def remember_candidate_once(candidate_id: str):
    from storage.memory_candidates import remember_once

    candidate = remember_once(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在或已处理")
    return {"code": 200, "messages": "仅本次记住", "data": {"candidate": candidate}}
