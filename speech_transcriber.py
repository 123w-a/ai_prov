# speech_transcriber.py：语音识别适配层
# 把阿里云百炼（DashScope）录音文件识别的细节封起来，
# 前端和 Agent 都只关心返回的 text，不碰 Agent 主逻辑 / 健康护栏 / RAG / 结构化输出。
#
# 设计原则（与项目 image_gen.py 降级风格一致）：
# - 无 DASHSCOPE_API_KEY / OSS 未配置 / 接口异常 → 返回 available=False，绝不抛异常、不阻断主流程。
# - 音频先上传到 OSS 拿公网可访问 URL（DashScope 文件识别要求传入可访问 URL），再提交异步识别任务。

import json
import os

import requests
from dashscope.audio.asr import Transcription
from http import HTTPStatus

import oss_utils


def _extract_text(result) -> str:
    """从 Transcription.wait 的真实返回里抽出纯文本。

    经实测（真实调用 paraformer-v2）确认的返回结构：
        result.output["results"] = [
            {"transcription_url": "https://dashscope-result-...json?签名", ...},
            ...
        ]
    每个 transcription_url 指向一个**单个 JSON 对象**（非 JSON-Lines），形如：
        {
          "file_url": "...",
          "properties": {...},
          "transcripts": [
            {"channel_id": 0, "text": "今天中午我想吃番茄炒蛋和青椒炒肉。",
             "sentences": [{"text": "今天中午我想吃番茄炒蛋和青椒炒肉。", ...}]}
          ]
        }
    需下载后取 transcripts[].text（或 transcripts[].sentences[].text）拼接。
    同时保留 JSON-Lines 兜底（个别旧版本/接口可能返回逐行 JSON）。
    """
    out = result.output
    # TranscriptionOutput 是 DictMixin，既支持 out["results"] 也支持 out.get("results")
    results = out.get("results") if hasattr(out, "get") else getattr(out, "results", [])
    if not results:
        return ""

    parts = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("transcription_url")
        if not url:
            # 兼容嵌套 output.results[].transcription_url
            nested = item.get("output") or {}
            if isinstance(nested, dict):
                for sub in (nested.get("results") or []):
                    if isinstance(sub, dict) and sub.get("transcription_url"):
                        url = sub.get("transcription_url")
                        break
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            # 单条结果下载失败不影响其它条
            continue
        # 优先按单个 JSON 对象解析（实测结构）
        try:
            obj = resp.json()
            for tr in (obj.get("transcripts") or []):
                t = tr.get("text")
                if t:
                    parts.append(t)
                else:
                    for s in (tr.get("sentences") or []):
                        if s.get("text"):
                            parts.append(s.get("text"))
            continue
        except ValueError:
            pass
        # 兜底：JSON-Lines（逐行 JSON）
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                t = o.get("text") or "".join(
                    s.get("text", "") for s in (o.get("sentences") or [])
                )
                if t:
                    parts.append(t)
            except json.JSONDecodeError:
                parts.append(line)
    return "".join(parts).strip()


def transcribe_audio(audio_bytes: bytes, filename: str, content_type: str) -> dict:
    """把一段音频转成文字。

    Returns:
        {
            "available": bool,   # 服务是否可用（有 key + 成功识别）
            "text": str,         # 识别出的文字（失败/不可用为空串）
            "message": str,      # 人类可读状态说明
        }
    """
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        return {
            "available": False,
            "text": "",
            "message": "未配置 DASHSCOPE_API_KEY，语音识别暂不可用",
        }

    # 1) 上传到 OSS 拿公网可访问 URL（DashScope 文件识别要求传入可访问 URL）
    #    显式指定扩展名：mimetypes 对 audio/mp3 等不识别会退化成 .jpg，
    #    导致 DashScope 误判媒体类型、识别为空。按 MIME 映射成正确音频后缀。
    _AUDIO_EXT = {
        "audio/wav": ".wav", "audio/x-wav": ".wav",
        "audio/mp3": ".mp3", "audio/mpeg": ".mp3",
        "audio/m4a": ".m4a", "audio/webm": ".webm",
    }
    try:
        ext = _AUDIO_EXT.get((content_type or "").lower())
        oss_url = oss_utils.upload_to_oss(audio_bytes, content_type or "audio/wav", ext=ext)
    except Exception as err:  # noqa: BLE001  —— OSS 未配置或网络异常，降级而非崩溃
        return {
            "available": False,
            "text": "",
            "message": f"音频上传到 OSS 失败：{err}",
        }

    # 2) 提交异步识别任务并等待完成
    model = os.getenv("SPEECH_MODEL", "paraformer-v2")
    language_hints = [
        h.strip()
        for h in os.getenv("SPEECH_LANGUAGE_HINTS", "zh,en").split(",")
        if h.strip()
    ]
    try:
        task_response = Transcription.async_call(
            model=model,
            file_urls=[oss_url],
            language_hints=language_hints,
            api_key=api_key,
        )
        if task_response.status_code != HTTPStatus.OK:
            return {
                "available": False,
                "text": "",
                "message": f"提交识别任务失败（{task_response.status_code}）：{getattr(task_response, 'message', '')}",
            }

        result = Transcription.wait(task_response, api_key=api_key)
        if result.status_code != HTTPStatus.OK:
            return {
                "available": False,
                "text": "",
                "message": f"识别任务未完成（{result.status_code}）：{getattr(result, 'message', '')}",
            }

        text = _extract_text(result)
    except Exception as err:  # noqa: BLE001
        return {
            "available": False,
            "text": "",
            "message": f"语音识别调用异常：{err}",
        }

    if not text:
        return {
            "available": True,
            "text": "",
            "message": "识别完成但未返回文字（音频可能无有效语音内容）",
        }

    return {
        "available": True,
        "text": text,
        "message": "语音识别成功",
    }
