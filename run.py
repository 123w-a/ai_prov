# run.py：后端启动入口。改完包结构后用这个文件启动。
# 重要：端口保持 8010，与前端 app.py 的 DEFAULT_API_URL (http://127.0.0.1:8010) 一致，否则前端连不上。
import uvicorn
from api.main_app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8010)
