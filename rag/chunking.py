"""RAG 切片层。"""

from __future__ import annotations

import re


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    return [part.strip() for part in parts if part.strip()]


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [
        text[index : index + max_chars].strip()
        for index in range(0, len(text), max_chars)
        if text[index : index + max_chars].strip()
    ]


def _tail(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0:
        return ""
    return text[-overlap_chars:].strip()


def chunk_by_paragraph(
    text: str,
    max_chars: int = 800,
    overlap_chars: int = 120,
) -> list[str]:
    """按段落合并切片，超长段落优先按句子、最后按字符切分。"""

    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars 必须大于等于 0 且小于 max_chars")

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text or "")
        if paragraph.strip()
    ]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
            continue
        sentences = _split_sentences(paragraph)
        if len(sentences) == 1:
            units.extend(_hard_split(paragraph, max_chars))
            continue
        for sentence in sentences:
            units.extend(
                _hard_split(sentence, max_chars)
                if len(sentence) > max_chars
                else [sentence]
            )

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if current and len(candidate) > max_chars:
            chunks.append(current)
            prefix = _tail(current, overlap_chars)
            current = f"{prefix}\n\n{unit}".strip() if prefix else unit
            if len(current) > max_chars:
                current = unit
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def chunk_by_heading(
    text: str,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[tuple[str, str]]:
    """按二级标题切片，并对过长章节做段落级二次切分。

    返回 ``[(标题, 正文), ...]``，保留文档开头的非标题内容，
    避免把有效的摘要或说明误删。
    """

    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")

    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text or ""))
    if not matches:
        return [("文档内容", chunk) for chunk in chunk_by_paragraph(text, max_chars, overlap_chars)]

    sections: list[tuple[str, str]] = []
    prefix = (text[: matches[0].start()]).strip()
    if prefix:
        sections.append(("文档概览", prefix))

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        if len(body) <= max_chars:
            sections.append((title, body))
            continue
        for part in chunk_by_paragraph(body, max_chars, overlap_chars):
            sections.append((title, part))
    return sections
