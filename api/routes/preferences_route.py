"""用户长期饮食偏好接口：读取 / 更新 data/preferences.txt。

这是前端可管理的轻量长期记忆入口，与 main.load_preferences 共用同一文件。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_PREFS_PATH = Path(__file__).resolve().parents[2] / "data" / "preferences.txt"


class PreferencesPayload(BaseModel):
    preferences: str = Field(..., max_length=5000)


@router.get("/preferences")
def get_preferences():
    text = ""
    if _PREFS_PATH.exists():
        text = _PREFS_PATH.read_text(encoding="utf-8")
    return {
        "code": 200,
        "messages": "偏好读取成功",
        "data": {"preferences": text},
    }


@router.put("/preferences")
def update_preferences(payload: PreferencesPayload):
    text = payload.preferences.replace("\r\n", "\n").replace("\r", "\n").strip()
    _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PREFS_PATH.write_text(text + "\n" if text else "", encoding="utf-8")
    return {
        "code": 200,
        "messages": "偏好已保存",
        "data": {"preferences": text},
    }