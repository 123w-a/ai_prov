"""用户长期饮食偏好 + 结构化健康画像接口。

preferences.txt：旧自由文本（保留兼容）
profile.json  ：P2 结构化画像（存在时注入优先级更高，见 main.load_preferences）
这是前端可管理的轻量长期记忆入口。
"""
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_PREFS_PATH = _DATA_DIR / "preferences.txt"
_PROFILE_PATH = _DATA_DIR / "profile.json"

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

@router.get("/preferences")
def get_preferences():
    text = ""
    if _PREFS_PATH.exists():
        text = _PREFS_PATH.read_text(encoding="utf-8")
    return {"code": 200, "messages": "偏好读取成功", "data": {"preferences": text}}

@router.put("/preferences")
def update_preferences(payload: PreferencesPayload):
    text = payload.preferences.replace("\r\n", "\n").replace("\r", "\n").strip()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PREFS_PATH.write_text(text + "\n" if text else "", encoding="utf-8")
    return {"code": 200, "messages": "偏好已保存", "data": {"preferences": text}}

@router.get("/profile")
def get_profile():
    """读取结构化健康画像；文件不存在时返回 exists=false 的空默认值。"""
    if not _PROFILE_PATH.exists():
        empty = HealthProfilePayload()
        return {"code": 200, "messages": "尚未建档", "data": {"exists": False, "profile": empty.model_dump()}}
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        profile = HealthProfilePayload(**(raw if isinstance(raw, dict) else {}))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"画像文件损坏：{exc}")
    return {"code": 200, "messages": "画像读取成功", "data": {"exists": True, "profile": profile.model_dump()}}

@router.put("/profile")
def update_profile(payload: HealthProfilePayload):
    """写入结构化健康画像（整档覆盖式保存）。过敏原为硬约束字段，前端应显著标注。"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(
        json.dumps(payload.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"code": 200, "messages": "画像已保存", "data": {"exists": True, "profile": payload.model_dump()}}