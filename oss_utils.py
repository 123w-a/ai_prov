import time
import uuid
import mimetypes
import os
from dotenv import load_dotenv
import alibabacloud_oss_v2 as oss

load_dotenv()

# 环境变量读取
ENDPOINT = os.getenv("OSS_ENDPOINT")
BUCKET_NAME = os.getenv("OSS_BUCKET")
ACCESS_KEY = os.getenv("OSS_ACCESS_KEY")
SECRET_KEY = os.getenv("OSS_SECRET_KEY")
BASE_URL = os.getenv("OSS_BASE_URL")

# ========== 关键修复：region 改为 cn-beijing ==========
cred_provider = oss.credentials.StaticCredentialsProvider(ACCESS_KEY, SECRET_KEY)
cfg = oss.config.load_default()
cfg.credentials_provider = cred_provider
cfg.region = "cn-beijing"  # 这里是Region ID，不是endpoint前缀
cfg.endpoint = ENDPOINT
client = oss.Client(cfg)


def upload_to_oss(file_bytes: bytes, content_type: str, ext: str = None) -> str:
    # ext 允许调用方显式指定扩展名。默认仍用 mimetypes 推断，
    # 但 mimetypes 对 audio/mp3 等不识别会退化成 .jpg，语音场景需显式传 .mp3/.wav 等，
    # 否则 DashScope 会按图片媒体类型误判导致识别为空。
    if not ext:
        ext = mimetypes.guess_extension(content_type) or ".jpg"
    object_key = f"upload/{int(time.time())}_{uuid.uuid4().hex}{ext}"
    try:
        resp = client.put_object(
            oss.PutObjectRequest(
                bucket=BUCKET_NAME,
                key=object_key,
                body=file_bytes,
                content_type=content_type
            )
        )
        full_url = f"{BASE_URL}/{object_key}"
        try:
            # Windows 控制台默认 GBK 编码，print emoji 会抛 UnicodeEncodeError；
            # 必须兜住，否则上层会把「打印失败」误判成「上传失败」，丢弃成品图。
            print(f"[oss] 上传成功：{object_key}")
            print(f"[oss] 访问链接：{full_url}")
        except Exception:
            pass
        return full_url
    except Exception as err:
        try:
            print(f"[oss] 上传失败：{err}")
        except Exception:
            pass
        raise err


if __name__ == "__main__":
    # 测试上传文本文件
    upload_to_oss(b"oss test content", "text/plain")
