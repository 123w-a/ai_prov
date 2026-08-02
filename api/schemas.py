# schemas.py：统一存放所有 Pydantic 响应/请求模型（前后端的数据结构契约）
from pydantic import BaseModel#所有类的父类

#模型响应参数模板
class ChatData(BaseModel):
    session_id: str#会话 ID
    answer: str  # AI 的回答文本；前端 app.py 读的是 data.answer


class ApiResponse(BaseModel):#传入前端的响应参数模板
    code: int#状态码
    messages: str#响应信息
    data: ChatData | None = None#传入前端的数据
