"""语料清洗层。

清洗的目标是去掉明显的噪声，同时尽量保留原文结构和可追溯信息。
它不负责切片、向量化或写入数据库。
"""

from __future__ import annotations

import re


_SEPARATOR_RE = re.compile(r"^\s*[-*_]{3,}\s*$")
_SOURCE_RE = re.compile(r"(?m)^\s*(?:来源|出处|参考来源)[：:]\s*(.+?)\s*$")
_NOISE_MARKERS = ("免责声明", "仅供参考", "不构成医疗", "版权声明")


def clean_text(raw: str) -> str:
    """清洗 Markdown/PDF 提取文本并保留 Markdown 标题结构。"""

    if not raw:
        return ""

    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # 只移除明确的免责声明块引用；普通引用内容保留，避免误删知识。
        if stripped.startswith(">"):
            quoted = stripped.lstrip("> ").strip()
            if any(marker in quoted for marker in _NOISE_MARKERS):
                continue
            line = quoted
            stripped = quoted

        if _SEPARATOR_RE.fullmatch(stripped):
            continue

        # 清除 PDF/网页常见的不可见字符，但不改动正文标点。
        line = line.replace("\u200b", "").replace("\ufeff", "")
        line = line.replace("\u00a0", " ")
        lines.append(line.rstrip())

    text = "\n".join(lines)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_source(text: str) -> str:
    """从正文中提取来源，供引用和 metadata 使用。"""

    match = _SOURCE_RE.search(text or "")
    return match.group(1).strip() if match else "未知来源"
