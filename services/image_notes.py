"""菜品图片说明的统一生成与清洗。"""

import re


AI_IMAGE_NOTE = "AI 生成示意图（非真实成品照，仅供样式参考）"

_NOTE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"AI\s*生成示意图"
    r"(?:\s*[（(]\s*非真实成品照\s*[，,]\s*仅供样式参考\s*[）)])?"
    r"|AI\s*生成图"
    r"|后台自动补图"
    r")",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_VISIBLE_TEXT_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")
_NEGATIVE_NOTE_RE = re.compile(
    r"(?:"
    r"没有(?:可用|找到|检索到|匹配到)?[^。！？!?；;\n]{0,24}(?:图|照片|成品照)"
    r"|没(?:有|找到|查)?[^。！？!?；;\n]{0,24}(?:图|照片|成品照)"
    r"|未能[^。！？!?；;\n]{0,24}(?:图|照片|成品照)"
    r"|无法[^。！？!?；;\n]{0,24}(?:图|照片|成品照)"
    r"|暂(?:无|时没有)[^。！？!?；;\n]{0,24}(?:图|照片|成品照)"
    r"|图片链接[^。！？!?；;\n]{0,12}(?:空|无效|失败)"
    r"|(?:因此|所以)?不放图"
    r"|不配图"
    r")"
)


def sanitize_image_note(note: str) -> str:
    """移除旧图注中的失败/无图结论，保留仍有用的口感描述。"""

    text = str(note or "").strip()
    if not text:
        return ""
    # 同一张图可能先写 AI 声明，再由后台补图追加来源说明。循环剥离
    # 开头连续的图注前缀，保证 build_image_note 连续调用仍然幂等。
    while True:
        stripped = _NOTE_PREFIX_RE.sub("", text, count=1).lstrip(
            " \t:：;；,，。"
        )
        if stripped == text:
            break
        text = stripped
    kept = []
    for fragment in _SENTENCE_SPLIT_RE.split(text):
        fragment = str(fragment or "").strip()
        if (
            not fragment
            or not _VISIBLE_TEXT_RE.search(fragment)
            or _NEGATIVE_NOTE_RE.search(fragment)
        ):
            continue
        if fragment not in kept:
            kept.append(fragment)
    return " ".join(kept).strip()


def build_image_note(
    image_ai_generated: bool,
    previous_note: str = "",
    *,
    source: str = "live",
) -> str:
    """生成与当前图片状态一致的图注，避免旧否定说明和新图同时出现。"""

    del source
    cleaned = sanitize_image_note(previous_note)
    if not image_ai_generated:
        return cleaned
    if not cleaned or cleaned == AI_IMAGE_NOTE:
        return AI_IMAGE_NOTE
    return f"{AI_IMAGE_NOTE} {cleaned}"
