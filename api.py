from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from pydantic import BaseModel

from main import ask_agent_with_image, ask_agent_with_text


app = FastAPI(title="ai私厨")

UPLOAD_DIR = Path("resources/uploads")#创建保存用户上传图片的目录为了持久性短期记忆
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


class ChatData(BaseModel):
    session_id: str
    assion: str


class ApiResponse(BaseModel):
    code: int
    messages: str
    data: ChatData | None = None


@app.get("/")
def health():
    return ApiResponse(
        code=200,
        messages="服务正常",
        data=None
    )


@app.post("/api/chat/image", response_model=ApiResponse)#规定接口返回的数据结构
async def chat_image(#适用于高并发场景async def  这里主要是接受前端发来的信息
    session_id: str = Form(...),#对话唯一ID
    message: str = Form(...),#聊天内容
    image: UploadFile | None = File(None)#图片文件
):
    if not message.strip() and image is None:
        raise HTTPException(
            status_code=400,
            detail="请至少输入文字或上传一张图片"
        )

    if image is not None:
        if image.content_type not in {#检查是否是合格图片
            "image/jpeg",
            "image/png",
            "image/webp"
        }:
            raise HTTPException(
                status_code=400,
                detail="只支持 JPG、PNG、WEBP 图片"
            )
        #这3行代码是保存图片到本地中能够持久性短期记忆
        suffix = Path(image.filename or "").suffix.lower()
        image_path = UPLOAD_DIR / f"{uuid4().hex}{suffix}"
        image_path.write_bytes(await image.read())

        answer = ask_agent_with_image(
            str(image_path),#将图片转为字符串
            message,#前端返回的聊天内容
            session_id#该会话的唯一ID
        )
    else:
        answer = ask_agent_with_text(
            message,#前端返回的聊天内容
            session_id#该会话的唯一ID
        )

    return ApiResponse(
        code=200,
        messages="请求成功",
        data=ChatData(
            session_id=session_id,
            assion=answer#返回给前端的答案
        )
    )
