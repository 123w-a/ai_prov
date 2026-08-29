# session_route.py：只负责"会话管理"CRUD（新建 / 列表 / 删除 / 清空 / 删单条）+ 回答满意度反馈
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import feedback_store
from feedback_store import read_events as _read_feedback_events, write_events as _write_feedback_events
from sessions_store import (
    create_session,
    list_sessions,
    delete_session,
    clear_session,
    delete_message,
    append_message,
    patch_message_feedback,
    star_message,
    list_starred,
    _read_session,
)
from main import image_bytes_to_oss_url

router = APIRouter()


class FeedbackPayload(BaseModel):
    """回答满意度：'up' | 'down'；重复提交同值表示取消（toggle）。"""
    rating: str = Field(..., pattern="^(up|down)$")


@router.post("/sessions")
def api_create_session():
    return {"session": create_session()}


@router.get("/sessions")
def api_list_sessions():
    # 前端侧栏渲染全部会话+消息，靠这个接口拉数据
    return {"sessions": list_sessions()}


@router.delete("/sessions/{sid}")
def api_delete_session(sid: str):
    # 彻底删除整个会话及其全部消息（硬删除，不可恢复）
    delete_session(sid)
    return {"ok": True, "msg": "会话已删除"}


@router.post("/sessions/{sid}/clear")
def api_clear_session(sid: str):
    # 只清空消息，保留会话外壳
    clear_session(sid)
    return {"ok": True, "msg": "会话消息已清空"}


@router.post("/sessions/{sid}/messages/{rec_id}/feedback")
def api_message_feedback(sid: str, rec_id: int, payload: FeedbackPayload):
    """回答满意度反馈：卡片 👍/👎；同值再点 = 取消。同时记入周统计事件文件。"""
    data = _read_session(sid)
    rec = (
        next((m for m in (data or {}).get("messages") or [] if m.get("id") == rec_id), None)
        if data else None
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="该轮对话不存在")

    prev = rec.get("feedback")
    if prev == payload.rating:
        found, current = patch_message_feedback(sid, rec_id, None)  # 同值再点 = 取消
    else:
        found, current = patch_message_feedback(sid, rec_id, payload.rating)
    if not found:
        raise HTTPException(status_code=404, detail="该轮对话不存在")

    events = _read_feedback_events()
    kept = [e for e in events if not (e["sid"] == sid and e["rec_id"] == rec_id)]
    if current == payload.rating:  # 新打分：记事件
        dish = None
        try:
            ans = json.loads(rec.get("answer") or "")
            recipes = ans.get("recipes") or []
            dish = str(recipes[0].get("name"))[:40] if recipes else None
        except Exception:
            dish = None
        if current == "down":  # 口味信号采集：被踩的菜名+原话里扫口味词典（失败不阻塞）
            try:
                from taste_store import detect_tastes, record_signals
                tastes = detect_tastes((dish or "") + " " + (rec.get("user_text") or ""))
                record_signals(tastes, sid, rec_id)
            except Exception:
                pass
        kept.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "sid": sid, "rec_id": rec_id,
            "rating": current, "dish": dish,
        })
    _write_feedback_events(kept)
    return {"code": 200, "data": {"feedback": current}}


class StarPayload(BaseModel):
    starred: bool


@router.post("/sessions/{sid}/messages/{rec_id}/star")
def api_message_star(sid: str, rec_id: int, payload: StarPayload):
    """收藏/取消收藏一条问答（复选语义由前端按钮态呈现）。"""
    found, current = star_message(sid, rec_id, payload.starred)
    if not found:
        raise HTTPException(status_code=404, detail="该轮对话不存在")
    return {"code": 200, "data": {"starred": current}}


@router.get("/favorites")
def api_favorites():
    """收藏夹：跨会话聚合全部收藏项（菜名/图/来源会话）。"""
    return {"code": 200, "data": {"favorites": list_starred()}}


@router.get("/feedback/weekly")
def api_feedback_weekly():
    """近 7 天回答满意度统计：up/down 计数与被踩菜名 top3。"""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    events = [
        e for e in _read_feedback_events()
        if str(e.get("ts", "")) >= cutoff
    ]
    up = sum(1 for e in events if e.get("rating") == "up")
    down_events = [e for e in events if e.get("rating") == "down"]
    dish_counts: dict = {}
    for e in down_events:
        dish = e.get("dish")
        if dish:
            dish_counts[dish] = dish_counts.get(dish, 0) + 1
    top_down = sorted(dish_counts.items(), key=lambda kv: -kv[1])[:3]
    return {
        "code": 200,
        "data": {
            "up": up, "down": len(down_events),
            "total": len(events),
            "down_dishes": ["".join([d, f"×{n}"]) if n > 1 else d for d, n in top_down],
        },
    }


@router.delete("/sessions/{sid}/messages/{msg_id}")
def api_delete_message(sid: str, msg_id: int):
    delete_message(sid, msg_id)
    return {"ok": True, "msg": "单条消息已删除"}


@router.post("/sessions/{sid}/messages")
async def api_append_message(
    sid: str,
    user_text: str = Form(...),
    answer: str = Form(...),
    time: str = Form(...),
    image_name: str = Form(None),
    image_type: str = Form(None),
    image: UploadFile | None = File(None),
):
    # 备用：前端手动补一条消息入库（常规流程由 chat_image 自动调用 append_message）
    image_url = None
    if image is not None:
        file_bytes = await image.read()
        image_url = image_bytes_to_oss_url(file_bytes, image.content_type)
    append_message(sid, user_text, answer, time, image_name, image_type, image_url)
    return {"ok": True, "msg": "消息入库成功"}
