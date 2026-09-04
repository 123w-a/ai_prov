"""一次性预下载 bge 中文 embedding 模型，供离线知识库预热使用。"""
import os

os.environ.pop("HF_HUB_OFFLINE", None)
os.environ.pop("TRANSFORMERS_OFFLINE", None)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from sentence_transformers import SentenceTransformer

model_name = os.getenv("KB_BGE_MODEL", "BAAI/bge-small-zh-v1.5")
SentenceTransformer(model_name)
print(f"bge model ready: {model_name}")
