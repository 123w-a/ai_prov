# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from fastapi.responses import StreamingResponse  # SSE 流式返回需要它
import json  # 把 token / finish / error 打包成 SSE 事件

from api.schemas import ApiResponse, ChatData
from main import (
    ask_agent_with_image_url,
    ask_agent_with_text,
    image_bytes_to_oss_url,
    stream_agent_with_image_url,
    stream_agent_with_text,
)
from sessions_store import append_message
from datetime import datetime

router = APIRouter()#创建一个小路由不做的话那就无法分文件来写接口


@router.get("/")
def health():
    return ApiResponse(code=200, messages="服务正常", data=None)


@router.post("/chat/image", response_model=ApiResponse)
async def chat_image(#接受前端返回的数据，异步操作的接口
    session_id: str = Form(...),#拿文字
    message: str = Form(...),
    image: UploadFile | None = File(None),#可接受图片也可以不接受图片，拿图片
    image_url: str | None = Form(None),  # 前端若已有 OSS URL（重新生成），直接传 URL 不再上传文件
):#只能是这样传参，否则会报错
    # 既没有文字也没有图片/URL 就直接拒绝，避免空请求打到昂贵的大模型
    if not message.strip() and image is None and not image_url:#既没有文字也没有图片就直接拒绝，避免空请求打到昂贵的大模型
        raise HTTPException(status_code=400, detail="请至少输入文字、上传图片或提供图片 URL")#直接报错给前端

    save_img_name = None  # 保存的图片文件名（如：avatar.jpg、photo123.png）
    save_img_type = None  # 图片文件类型/后缀/MIME类型（如 jpg/png/jpeg、image/jpeg）
    save_img_url = image_url  # 最终落库的图片 OSS URL（优先用前端传入的，否则本地上传后得到）

    if image_url:
        # 前端已提供 OSS URL：视觉模型直接读公网 URL，不再重复上传
        answer = ask_agent_with_image_url(image_url, message, session_id)
    elif image is not None:
        # 白名单校验 MIME，挡掉非图片和可能的恶意文件
        # 部分浏览器/系统可能把 .jpg 标成 image/jpg，也接受；webp/png 保持标准 MIME
        if image.content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
            raise HTTPException(status_code=400, detail="只支持 JPG、PNG、WEBP 图片")#报错给前端
        file_bytes = await image.read()#获取图片二进制数据 await 异步读取文件进行高并发
        save_img_name = image.filename  # 用户上传原始文件名
        save_img_type = image.content_type  # 图片MIME类型(image/png等)
        save_img_url = image_bytes_to_oss_url(file_bytes, image.content_type)
        answer = ask_agent_with_image_url(save_img_url, message, session_id)
    else:
        answer = ask_agent_with_text(message, session_id)

    # 重要：每轮问答自动落库，前端刷新/重进都能从后端恢复历史
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_message(
        sid=session_id,#会话ID
        user_text=message,#用户输入
        answer=answer,#AI回复
        time=now_time,#一轮对话创建的时间
        image_name=save_img_name,#图片名
        image_type=save_img_type,#图片后缀
        image_url=save_img_url,#图片 OSS URL（替代原来的 base64 二进制流）
    )

    return ApiResponse(
        code=200,
        messages="请求成功",
        data=ChatData(session_id=session_id, answer=answer),
    )


@router.post("/chat/stream")
async def chat_stream(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
    image_url: str | None = Form(None),
):
    # 流式对话接口：和 /chat/image 一样接收图片/文字/图片URL，但用 SSE 逐 token 推给前端
    # 工具调用(搜索/读文件/OSS)是阻塞整块返回，无法拆流；只有最后一轮 LLM 汇总逐字流出
    if not message.strip() and image is None and not image_url:
        raise HTTPException(status_code=400, detail="请至少输入文字、上传图片或提供图片 URL")

    save_img_name = None
    save_img_type = None
    save_img_url = image_url

    if image_url:
        # 前端已提供 OSS URL，直接复用
        pass
    elif image is not None:
        # 白名单校验 MIME，挡掉非图片和可能的恶意文件
        if image.content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
            raise HTTPException(status_code=400, detail="只支持 JPG、PNG、WEBP 图片")
        file_bytes = await image.read()
        save_img_name = image.filename
        save_img_type = image.content_type
        save_img_url = image_bytes_to_oss_url(file_bytes, image.content_type)

    def event_generator():
        # 两段式流式：先把 chef_think 的正文逐 token 推出去（打字机），
        # 再推 structuring 信号让前端切骨架屏，最后把 structure_answer 的整包 JSON 推给前端画卡片
        full_parts = []       # 正文 token 碎片（兜底落库用）
        final_answer = None   # structure_answer 节点产出的 ChefAnswer JSON 字符串
        try:
            if save_img_url:
                token_src = stream_agent_with_image_url(save_img_url, message, session_id)
            else:
                token_src = stream_agent_with_text(message, session_id)
            for kind, payload in token_src:
                if kind == "token":
                    full_parts.append(payload)
                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"
                elif kind == "answer":
                    final_answer = payload
                    # 先通知前端"正文说完了，正在整理卡片"，再推整包 JSON
                    yield f"data: {json.dumps({'structuring': True}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'answer': json.loads(payload)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            # 出错时推一个 error 事件，前端据此提示，避免一直转圈
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

        # 整轮结束落库：有结构化 JSON 就存 JSON（前端画卡片）；
        # 没有（结构化链降级/未触发）就存正文 markdown（前端走旧渲染），双格式兼容
        answer = final_answer if final_answer else "".join(full_parts)
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
        yield f"data: {json.dumps({'finish': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

