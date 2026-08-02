# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
# 统一入口：单端点 + stream 布尔开关，流式/非流式复用同一张 LangGraph、同一套结构化链路。
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse  # SSE 流式 / 非流式 JSON 都要
import json  # 把 token / finish / error 打包成 SSE 事件

from agent import agent  # 直接拿图实例，非流式用 agent.ainvoke
from main import (
    build_human_message,
    stream_agent,
    ask_agent,
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
    """每轮问答自动落库：前端刷新/重进都能从后端恢复历史。流式/非流式共用。"""
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


async def _non_stream_result(human_message, session_id, message,
                             save_img_name, save_img_type, save_img_url):
    """非流式核心：agent.ainvoke 一次性跑完状态机，返回 (原始 JSON 字符串, 解析后 dict)。
    流式/非流式两条非流式路径（/chat 的 stream=False 与 /chat/image 别名）都复用它，
    保证 agent 调用、落库、结构化解析逻辑 100% 一致。"""
    last_content = await ask_agent(human_message, session_id)
    _save_record(session_id, message, last_content, save_img_name, save_img_type, save_img_url)
    try:
        full_data = json.loads(last_content)
    except Exception:
        # 极端降级：结构化链彻底失败，回退成纯文本对象
        full_data = {"content": last_content}
    return last_content, full_data


@router.get("/")
def health():
    return {"code": 200, "messages": "服务正常", "data": None}


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
    stream: bool = Form(True),  # 默认流式（兼容旧前端）；传 false 则一次性返回完整 JSON
):
    """统一聊天入口：用 stream 参数切换两种执行范式，底层完全复用。

    - stream=True  -> agent.stream() 做 SSE 两段式流式（打字机 + 整包卡片 JSON）
    - stream=False -> agent.ainvoke() 一次性跑完状态机，直接返回 Pydantic 约束的 ChefAnswer JSON
    两种模式都经过 structure_answer 结构化节点，输出都是合规 JSON，前端单渲染路径。
    """
    if not message.strip() and image is None and not image_url:
        raise HTTPException(status_code=400, detail="请至少输入文字、上传图片或提供图片 URL")

    save_img_name, save_img_type, save_img_url = await _handle_image(image, image_url)
    human_message = build_human_message(message, save_img_url)
    config = {"configurable": {"thread_id": session_id}}

    if stream:
        # ---- 流式分支：复用 _stream_agent 生成器，SSE 逐事件推 ----
        def event_generator():
            full_parts = []     # 正文 token 碎片（兜底落库用）
            final_answer = None  # structure_answer 节点产出的 ChefAnswer JSON 字符串
            try:
                for kind, payload in stream_agent(human_message, session_id):
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

    # ---- 非流式分支：返回干净的 ChefAnswer JSON（适合第三方系统对接 / 后端调试） ----
    _last_content, full_data = await _non_stream_result(
        human_message, session_id, message, save_img_name, save_img_type, save_img_url
    )
    return JSONResponse(content=full_data)


# --------------------------------------------------------------------------- #
#  兼容旧前端：保留 /chat/stream 与 /chat/image 作为薄别名，避免改前端。
#  - /chat/stream  -> 永远流式（前端打字机）
#  - /chat/image   -> 永远非流式，且返回旧信封 {code,data.answer}，兼容 api_chat()
#  若日后前端统一改调 /chat（带 stream 参数），这两个别名可随时删除。
# --------------------------------------------------------------------------- #
@router.post("/chat/stream")
async def chat_stream_alias(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
):
    return await chat(session_id=session_id, message=message, image=image, image_url=image_url, stream=True)


@router.post("/chat/image")
async def chat_image_alias(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
):
    save_img_name, save_img_type, save_img_url = await _handle_image(image, image_url)
    human_message = build_human_message(message, save_img_url)
    last_content, _full_data = await _non_stream_result(
        human_message, session_id, message, save_img_name, save_img_type, save_img_url
    )
    # 旧信封格式：api_chat() 依赖 code / data.answer 字段
    return {
        "code": 200,
        "messages": "请求成功",
        "data": {"session_id": session_id, "answer": last_content},
    }
