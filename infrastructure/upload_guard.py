"""上传护栏：体积上限 + 真实文件格式嗅探。

为什么不能只看 ``UploadFile.content_type``：
1. 它是**客户端自己声明的**，随便改；
2. 它拦不住体积——`await upload.read()` 会把超大文件一次性读进内存；
3. 声明 image/jpeg 实际传别的内容，会一路进到视觉模型或 OSS。

所以这里做两件事：分块读并卡上限、按文件头判断真实类型。
"""

from __future__ import annotations

import os
from typing import Iterable

from fastapi import HTTPException, UploadFile

# 单张图片上限（默认 8MB）；请求体总量上限在 main_app 的中间件里另有一层粗筛。
MAX_IMAGE_BYTES = int(os.getenv("CHEF_MAX_UPLOAD_MB", "8")) * 1024 * 1024
MAX_AUDIO_BYTES = int(os.getenv("SPEECH_MAX_MB", "10")) * 1024 * 1024

# 分块大小：够大以减少 await 次数，够小以免超限前就吃满内存。
_CHUNK_BYTES = 256 * 1024

IMAGE_MIME_WHITELIST = {"image/jpeg", "image/jpg", "image/png", "image/webp"}

_MAGIC_PREFIXES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(head: bytes) -> str:
    """按文件头判断真实图片类型；识别不出返回空串。

    只取前若干字节即可，不需要读完整个文件。
    """
    for signature, mime in _MAGIC_PREFIXES:
        if head.startswith(signature):
            return mime
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if len(head) >= 12 and head[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "image/heic"
    return ""


def _too_large(limit: int, kind: str) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"{kind}超过 {limit // (1024 * 1024)}MB 上限，请压缩后再上传",
    )


async def read_upload_limited(
    upload: UploadFile,
    limit: int = MAX_IMAGE_BYTES,
    kind: str = "图片",
) -> bytes:
    """异步分块读取上传内容，超过 limit 立刻 413（不把整个文件读进内存）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _too_large(limit, kind)
        chunks.append(chunk)
    return b"".join(chunks)


def read_spooled_limited(
    fileobj,
    limit: int = MAX_AUDIO_BYTES,
    kind: str = "音频",
) -> bytes:
    """同步版（供同步路由使用）：同样是分块读 + 卡上限。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = fileobj.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _too_large(limit, kind)
        chunks.append(chunk)
    return b"".join(chunks)


async def validate_image_upload(
    upload: UploadFile,
    whitelist: Iterable[str] = IMAGE_MIME_WHITELIST,
    limit: int = MAX_IMAGE_BYTES,
) -> tuple[bytes, str]:
    """校验并读取图片：返回 (字节, 真实 MIME)。

    三道关卡：声明类型在白名单 → 实际体积在上限内 → 文件头确实是图片。
    真实 MIME 以文件头为准，避免"声明 jpeg、实际别的格式"混进下游。
    """
    allowed = set(whitelist)
    declared = (upload.content_type or "").strip().lower()
    if declared and declared not in allowed:
        raise HTTPException(
            status_code=400,
            detail="只支持 JPG、PNG、WEBP 图片",
        )
    data = await read_upload_limited(upload, limit=limit, kind="图片")
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    sniffed = sniff_image_type(data[:32])
    if not sniffed:
        raise HTTPException(
            status_code=400,
            detail="文件内容不是有效图片（仅凭扩展名或声明类型不能通过校验）",
        )
    if sniffed not in allowed:
        raise HTTPException(status_code=400, detail=f"图片真实格式 {sniffed} 不在支持范围内")
    return data, sniffed
