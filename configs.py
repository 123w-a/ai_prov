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
