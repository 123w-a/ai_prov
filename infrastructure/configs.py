import os#操控文件
from dotenv import load_dotenv#读取环境变量

load_dotenv()#加载环境变量

MODEL_CONFIGS = {#模型配置
    "gpt": {
        "api_key": os.getenv("CHAT_API_KEY"),
        "base_url": os.getenv("CHAT_BASE_URL"),
        "model_name": os.getenv("CHAT_MODE_NAME"),
    },
    "deepseek": {
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        # 缺省时必须落到官方端点：ChatOpenAI 对 None 会静默回退 api.openai.com，
        # 导致 deepseek key 打错门（40s-167s 假慢/黑洞的真凶）。
        "base_url": os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        "model_name": os.getenv("DEEPSEEK_MODE_NAME"),
    },
    # 2026-10-08 用户指令「切成mimo」：走本机 commandcode-proxy（与 DSH 同一网关），
    # key 从 DSH 凭据库搬入 .env 的 MIMO_API_KEY（值不在源码/日志中出现）。
    "mimo": {
        "api_key": os.getenv("MIMO_API_KEY"),
        "base_url": os.getenv("MIMO_BASE_URL") or "http://127.0.0.1:3050/v1",
        "model_name": os.getenv("MIMO_MODE_NAME") or "xiaomi/mimo-v2.6-flash",
    },
}

VISION_CONFIGS = {
    "qwen": {
        # 视觉链路固定在国产模型上；复用已有 DashScope Key，避免中转站波动。
        "api_key": os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
        "base_url": os.getenv("QWEN_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_name": os.getenv("QWEN_MODE_NAME")
        or "qwen3-vl-235b-a22b-instruct",
    },
}

# RAG 知识库配置（rag/ingest.py 与 rag/retriever.py 均从 configs.KB_CONFIG 读取，
# 未定义时它们各自使用默认值；这里按 配置key.md 统一收口）
KB_CONFIG = {
    "corpus_dir": os.getenv("KB_CORPUS_DIR", "kb"),
    "kb_dir": os.getenv("KB_DIR", "kb/chroma"),
    "collection_name": os.getenv("KB_COLLECTION", "dietary_kb"),
    "embedding_backend": os.getenv("KB_EMBEDDING", "bge"),
    "chunk_size": int(os.getenv("KB_CHUNK_SIZE", "1200")),
    "chunk_overlap": int(os.getenv("KB_CHUNK_OVERLAP", "120")),
    "preview_dir": os.getenv("KB_PREVIEW_DIR", "resources/cleaned_preview"),
    "enable_rerank": os.getenv("KB_ENABLE_RERANK", "1") == "1",
}
