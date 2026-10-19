import io
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


# ========== 上传前压图：成品图统一转 WebP q90 ==========
# 为什么改这里：OSS 真正的计费项是「外网流出流量」（0.5 元/GB），存储只有 0.12 元/GB/月。
# 通义万相出的是 1024x1024 PNG，单张 1.1~1.7MB；转 WebP q90 后实测 84~213KB，体积降 88~93%，
# 外网流量同比例下降，首屏出图明显更快（移动端尤其明显）。
# 观感依据：3 张真实成品图实测 PSNR 39.0~42.4dB；1024 图在手机上按 400~600px 显示时
# 浏览器缩小采样会把这些差异抹掉，肉眼不可辨。q85 以下油光/焦边开始被抹平，故默认卡在 q90。
WEBP_QUALITY = int(os.getenv("OSS_IMAGE_WEBP_QUALITY", "90"))
# 节省不足 8% 就不换格式：小图或已高度压缩的图换 WebP 可能是负优化（体积反而更大）
WEBP_MIN_GAIN = float(os.getenv("OSS_IMAGE_WEBP_MIN_GAIN", "0.08"))
# method 0~6：越大压得越狠但越慢。1024 图在 method=4 约 100~200ms，相对上传耗时可忽略。
WEBP_METHOD = int(os.getenv("OSS_IMAGE_WEBP_METHOD", "4"))


def _pil_image():
    """延迟导入 Pillow。

    OSS 模块同时承担语音上传，不该因为环境里没装 Pillow 就整体导入失败；
    惰性导入让「没 Pillow」退化成「不压缩、照常上传」，而不是报错。
    """
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def compress_image_to_webp(file_bytes: bytes, quality: int = None) -> tuple:
    """把 PNG/JPEG 图片字节转成 WebP，返回 (新字节, 新 content_type, 新扩展名)。

    只处理 PNG/JPEG：
      - GIF 转了会丢掉动画，直接原样返回；
      - WebP 本身已是目标格式，原样返回（避免二次有损）；
      - 语音、文本等非图片，magic bytes 不匹配，原样返回。

    返回的 content_type / ext 为 None 表示「没压缩，沿用调用方原来的值」。
    压缩只是降本优化，任何异常都整体原样返回——绝不允许它阻断上传。
    """
    if not file_bytes or len(file_bytes) < 1024:
        return file_bytes, None, None
    is_png = file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = file_bytes.startswith(b"\xff\xd8\xff")
    if not (is_png or is_jpeg):
        return file_bytes, None, None
    Image = _pil_image()
    if Image is None:
        return file_bytes, None, None
    q = WEBP_QUALITY if quality is None else quality
    try:
        with Image.open(io.BytesIO(file_bytes)) as im:
            im.load()
            # 带透明通道的必须保留 alpha，否则 PNG 的透明底会被填成黑色
            has_alpha = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info
            )
            if has_alpha:
                im = im.convert("RGBA")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="WEBP", quality=q, method=WEBP_METHOD)
            out = buf.getvalue()
    except Exception:
        return file_bytes, None, None
    if not out or len(out) >= len(file_bytes) * (1.0 - WEBP_MIN_GAIN):
        return file_bytes, None, None
    return out, "image/webp", ".webp"


def upload_to_oss(file_bytes: bytes, content_type: str, ext: str = None,
                  optimize_image: bool = True) -> str:
    # ext 允许调用方显式指定扩展名。默认仍用 mimetypes 推断，
    # 但 mimetypes 对 audio/mp3 等不识别会退化成 .jpg，语音场景需显式传 .mp3/.wav 等，
    # 否则 DashScope 会按图片媒体类型误判导致识别为空。
    #
    # optimize_image=True 时，PNG/JPEG 会先转 WebP q90 再上传（成品图 1.4MB -> ~0.14MB）。
    # 非图片（语音/文本）、GIF、本来就是 WebP 的，compress_image_to_webp 会原样返回，
    # content_type 与 ext 都不动，行为与改造前完全一致。
    if optimize_image:
        compressed, webp_type, webp_ext = compress_image_to_webp(file_bytes)
        if webp_type:
            file_bytes = compressed
            content_type = webp_type
            if not ext:
                ext = webp_ext
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
        return full_url
    except Exception as err:

        raise err


if __name__ == "__main__":
    # 测试上传文本文件
    upload_to_oss(b"oss test content", "text/plain")
