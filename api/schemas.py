# schemas.py：统一存放所有 Pydantic 响应/请求模型（前后端的数据结构契约）
from pydantic import BaseModel


class ChatData(BaseModel):
    session_id: str
    answer: str  # AI 的回答文本；前端 app.py 读的是 data.answer


class ApiResponse(BaseModel):
    code: int
    messages: str
    data: ChatData | None = None  # 失败时 data 为 None
