# api/routes/speech_route.py：语音识别路由
# POST /api/transcribe
# 只做三件事：校验音频 MIME/大小 → 调用 speech_transcriber → 用统一信封返回。
# 风格与 service_route.py 保持一致（code / messages / data 信封）。
import os

from fastapi import APIRouter, File, UploadFile, HTTPException

from api.schemas import TranscribeData, TranscribeResponse
from speech_transcriber import transcribe_audio

router = APIRouter()

ALLOWED_AUDIO_MIME = {
    "audio/wav",
    "audio/mp3",
    "audio/m4a",
    "audio/webm",
    "audio/x-wav",
}
# 额外按扩展名兜底，兼容浏览器给出的奇怪 content_type
ALLOWED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".webm"}

MAX_BYTES = int(os.getenv("SPEECH_MAX_MB", "10")) * 1024 * 1024


@router.post("/transcribe", response_model=TranscribeResponse)
def transcribe(audio: UploadFile = File(...)):
    content_type = audio.content_type or ""
    ext = os.path.splitext(audio.filename or "")[1].lower()

    if content_type not in ALLOWED_AUDIO_MIME and ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(status_code=400, detail="不支持的音频格式")

    audio_bytes = audio.file.read()
    if len(audio_bytes) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="音频超过大小限制")

    result = transcribe_audio(audio_bytes, audio.filename or "audio", content_type)

    if not result.get("available"):
        # 未配置 key / OSS 失败 / 接口异常 → 503，明确提示“暂未配置/不可用”
        return TranscribeResponse(
            code=503,
            messages=result.get("message", "语音识别暂未配置"),
            data=TranscribeData(text="", provider="dashscope", available=False),
        )

    return TranscribeResponse(
        code=200,
        messages=result.get("message", "语音识别成功"),
        data=TranscribeData(
            text=result.get("text", ""),
            provider="dashscope",
            available=True,
        ),
    )
