# chat_route.py：只负责"AI 对话"这一类接口（图片/文本 -> 大模型 -> 存库）
from uuid import uuid4
from pathlib import Path
from fastapi import APIRouter, File, Form, UploadFile, HTTPException

from api.schemas import ApiResponse, ChatData
from main import ask_agent_with_image, ask_agent_with_text
from sessions_store import append_message
from datetime import datetime
from api.main_app import UPLOAD_DIR

router = APIRouter()


@router.get("/")
def health():
    return ApiResponse(code=200, messages="服务正常", data=None)


@router.post("/chat/image", response_model=ApiResponse)
async def chat_image(
    session_id: str = Form(...),
    message: str = Form(...),
    image: UploadFile | None = File(None),
):
    # 既没有文字也没有图片就直接拒绝，避免空请求打到昂贵的大模型
    if not message.strip() and image is None:
        raise HTTPException(status_code=400, detail="请至少输入文字或上传一张图片")

    save_img_name = None
    save_img_type = None
    save_img_bytes = None
    local_img_path = None

    if image is not None:
        # 白名单校验 MIME，挡掉非图片和可能的恶意文件
        # 部分浏览器/系统可能把 .jpg 标成 image/jpg，也接受；webp/png 保持标准 MIME
        if image.content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
            raise HTTPException(status_code=400, detail="只支持 JPG、PNG、WEBP 图片")
        suffix = Path(image.filename or "").suffix.lower()
        image_path = UPLOAD_DIR / f"{uuid4().hex}{suffix}"
        file_bytes = await image.read()
        image_path.write_bytes(file_bytes)

        local_img_path = str(image_path)
        save_img_name = image.filename
        save_img_type = image.content_type
        save_img_bytes = file_bytes

        # main.py 内部会把本地图上传到 OSS 拿到 URL，再喂给视觉大模型
        answer = ask_agent_with_image(local_img_path, message, session_id)
    else:
        answer = ask_agent_with_text(message, session_id)

    # 重要：每轮问答自动落库，前端刷新/重进都能从后端恢复历史
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    append_message(
        sid=session_id,
        user_text=message,
        answer=answer,
        time=now_time,
        image_name=save_img_name,
        image_type=save_img_type,
        image_data=save_img_bytes,
    )

    return ApiResponse(
        code=200,
        messages="请求成功",
        data=ChatData(session_id=session_id, answer=answer),
    )
