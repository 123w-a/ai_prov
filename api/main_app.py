# main_app.py：FastAPI 总入口。只做三件事：建 app 实例、初始化 DB、挂载子路由。
from fastapi import FastAPI
from sessions_store import init_db

app = FastAPI(title="ai私厨")#创键fastapi对象

# 重要：必须在 app 创建后、接收请求前初始化会话目录，否则首次插入会找不到 sessions/ 目录
init_db()

# 重要：子路由放在文件末尾导入，避免循环依赖
from api.routes.chat_route import router as chat_router
from api.routes.session_route import router as session_router

# 重要：prefix="/api" 让路由里只写 "/chat/image"，最终对外暴露为 /api/chat/image，
#统一向外面暴露接口
app.include_router(chat_router, prefix="/api")
app.include_router(session_router, prefix="/api")
