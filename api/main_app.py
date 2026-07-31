# main_app.py：FastAPI 总入口。只做三件事：建 app 实例、初始化 DB、挂载子路由。
from pathlib import Path
from fastapi import FastAPI
from sessions_store import init_db

# 上传图片先落到本地临时目录，再由 main.py 转成 OSS 链接喂给大模型
UPLOAD_DIR = Path("resources/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="ai私厨")

# 重要：必须在 app 创建后、接收请求前建表，否则首次插入会报 "no such table"
init_db()

# 重要：子路由放在文件末尾导入，避免循环依赖——
# 因为 routes 里会反向 import 本文件的 UPLOAD_DIR，若写在顶部会先引用到未定义的对象。
from api.routes.chat_route import router as chat_router
from api.routes.session_route import router as session_router

# 重要：prefix="/api" 让路由里只写 "/chat/image"，最终对外暴露为 /api/chat/image，
# 与旧 api.py 的地址完全一致，前端一行都不用改。
app.include_router(chat_router, prefix="/api")
app.include_router(session_router, prefix="/api")
