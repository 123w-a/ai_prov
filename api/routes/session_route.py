# session_route.py：只负责"会话管理"CRUD（新建 / 列表 / 删除 / 清空 / 删单条）
from fastapi import APIRouter, File, Form, UploadFile

from sessions_store import (
    create_session,
    list_sessions,
    delete_session,
    clear_session,
    delete_message,
    append_message,
)
from main import image_bytes_to_oss_url

router = APIRouter()


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
