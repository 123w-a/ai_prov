# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
# 当前只保留流式分支（SSE 打字机 + 整包卡片 JSON），非流式分支已删除。
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
import json  # 把 token / structuring / answer / finish 打包成 SSE 事件
import queue
import threading

from main import (
    build_human_message,
    stream_agent,
    image_bytes_to_oss_url,
)
from sessions_store import append_message
from datetime import datetime

router = APIRouter()  # 分文件写接口的小路由

# 图片 MIME 白名单：挡掉非图片和可能的恶意文件
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


async def _handle_image(image: UploadFile | None, image_url: str | None):
    """统一处理图片上传：返回 (save_img_name, save_img_type, save_img_url)。
    优先用前端已传的 OSS URL（重新生成时不重复上传），否则把上传文件存到 OSS。"""
    save_img_name = None
    save_img_type = None
    save_img_url = image_url
    if image_url:
        pass  # 前端已提供 OSS URL，视觉模型直接读公网地址
    elif image is not None:
        if image.content_type not in ALLOWED_MIME:
            raise HTTPException(status_code=400, detail="只支持 JPG、PNG、WEBP 图片")
        file_bytes = await image.read()
        save_img_name = image.filename
        save_img_type = image.content_type
        save_img_url = image_bytes_to_oss_url(file_bytes, image.content_type)
    return save_img_name, save_img_type, save_img_url


def _save_record(session_id, message, answer, save_img_name, save_img_type, save_img_url):
    """每轮问答自动落库：前端刷新/重进都能从后端恢复历史。流式共用。"""
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_message(
        sid=session_id,
        user_text=message,
        answer=answer,
        time=now_time,
        image_name=save_img_name,
        image_type=save_img_type,
        image_url=save_img_url,
    )


@router.get("/")
def health():
    return {"code": 200, "messages": "服务正常", "data": None}


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
):
    """统一聊天入口：仅流式分支。
    - SSE 逐事件推：正文 token -> 打字机；'structuring' -> 卡片占位动画；'answer' -> 整包 ChefAnswer JSON 渲染卡片
    - 全程走同一张 LangGraph + structure_answer 结构化节点
    """
    if not message.strip() and image is None and not image_url:
        raise HTTPException(status_code=400, detail="请至少输入文字、上传图片或提供图片 URL")

    save_img_name, save_img_type, save_img_url = await _handle_image(image, image_url)
    human_message = build_human_message(message, save_img_url)
    config = {"configurable": {"thread_id": session_id}}

    def event_generator():
        full_parts = []     # 正文 token 碎片（兜底落库用）
        final_answer = None  # structure_answer 节点产出的 ChefAnswer JSON 字符串
        events = queue.Queue()
        finished = object()

        def run_agent():
            try:
                for item in stream_agent(human_message, session_id):
                    events.put(("item", item))
            except Exception as exc:
                events.put(("error", exc))
            finally:
                events.put(("done", finished))

        # Agent 内部可能在联网搜索、图片下载或结构化模型调用中等待较久。
        # 放到后台线程后，主生成器可以每隔几秒发送心跳，避免前端误判为断线。
        threading.Thread(target=run_agent, daemon=True).start()
        yield f"data: {json.dumps({'status': 'working'}, ensure_ascii=False)}\n\n"

        try:
            while True:
                try:
                    event_type, event = events.get(timeout=10)
                except queue.Empty:
                    yield f"data: {json.dumps({'heartbeat': True}, ensure_ascii=False)}\n\n"
                    continue

                if event_type == "error":
                    raise event
                if event_type == "done":
                    break

                kind, payload = event
                if kind == "token":
                    full_parts.append(payload)
                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                elif kind == "answer":
                    final_answer = payload
                    # 先通知前端"正文说完了，正在整理卡片"，再推整包 JSON
                    yield f"data: {json.dumps({'structuring': True}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'answer': json.loads(payload)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

        # 整轮结束落库：有结构化 JSON 就存 JSON（前端画卡片）；
        # 没有（结构化链降级/未触发）就存正文 markdown（前端走旧渲染），双格式兼容
        answer = final_answer if final_answer else "".join(full_parts)
        _save_record(session_id, message, answer, save_img_name, save_img_type, save_img_url)
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
