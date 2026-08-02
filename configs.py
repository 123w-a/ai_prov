import os#操控文件
from dotenv import load_dotenv#读取环境变量

load_dotenv()#加载环境变量

MODEL_CONFIGS = {#模型配置
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "base_url": os.getenv("DEEPSEEK_BASE_URL"),
        "model_name": os.getenv("DEEPSEEK_MODE_NAME"),
    },
    "gpt": {
        "api_key": os.getenv("CHAT_API_KEY"),
        "base_url": os.getenv("CHAT_BASE_URL"),
        "model_name": os.getenv("CHAT_MODE_NAME"),
    },
}