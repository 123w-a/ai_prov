"""RAG 切片层。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParentChildChunk:
    """父级完整知识单元 + 用于检索的子片段。"""

    parent_id: str
    parent_text: str
    child_text: str
    section: str
    atomic_parent: bool


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


_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+\S|第[一二三四五六七八九十百]+[章节篇]\s*|"
    r"[一二三四五六七八九十]+、\s*\S)"
)
_ITEM_RE = re.compile(
    r"^(?:[-*+]\s+\S|\(?\d+[.)、]\s*\S|"
    r"[（(][一二三四五六七八九十\d]+[）)]\s*\S|"
    r"[一二三四五六七八九十]+[、.]\s*\S|[①-⑳]\s*\S)"
)
_TABLE_LINE_RE = re.compile(r"^(?:\|.*\||\S+(?:\s{2,}\S+){1,})\s*$")
_PAGE_MARKER_RE = re.compile(r"<<<PAGE:(\d+)>>>")


def _section_title(text: str) -> str:
    for line in (text or "").splitlines():
        clean = re.sub(r"<<<PAGE:\d+>>>", "", line).strip()
        if clean:
            return clean[:80]
    return "正文内容"


def _parent_child_units(
    text: str,
) -> list[tuple[str, bool, str]]:
    """拆出完整表格/条目 parent；普通段落不做跨段捆绑。"""

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    units: list[tuple[str, bool, str]] = []
    section = "正文内容"
    index = 0
    current_page = ""

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        page_match = _PAGE_MARKER_RE.fullmatch(line)
        if page_match:
            current_page = f"<<<PAGE:{int(page_match.group(1))}>>>"
            index += 1
            continue

        if _HEADING_RE.match(line):
            section = re.sub(r"<<<PAGE:\d+>>>", "", line).strip()[:80] or section
            units.append((f"{current_page}\n{line}".strip(), True, section))
            index += 1
            continue

        if _TABLE_LINE_RE.match(line):
            block = [line]
            index += 1
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate:
                    index += 1
                    continue
                candidate_page = _PAGE_MARKER_RE.fullmatch(candidate)
                if candidate_page:
                    current_page = f"<<<PAGE:{int(candidate_page.group(1))}>>>"
                    block.append(current_page)
                    index += 1
                    continue
                if not _TABLE_LINE_RE.match(candidate):
                    break
                block.append(candidate)
                index += 1
            units.append(
                (f"{current_page}\n{chr(10).join(block)}".strip(), True, section)
            )
            continue

        if _ITEM_RE.match(line):
            block = [line]
            index += 1
            while index < len(lines):
                candidate = lines[index].strip()
                if not candidate:
                    break
                candidate_page = _PAGE_MARKER_RE.fullmatch(candidate)
                if candidate_page:
                    current_page = f"<<<PAGE:{int(candidate_page.group(1))}>>>"
                    block.append(candidate)
                    index += 1
                    continue
                if _HEADING_RE.match(candidate) or _ITEM_RE.match(candidate):
                    break
                if _TABLE_LINE_RE.match(candidate):
                    break
                block.append(candidate)
                index += 1
            units.append(
                (f"{current_page}\n{chr(10).join(block)}".strip(), True, section)
            )
            continue

        block = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            candidate_page = _PAGE_MARKER_RE.fullmatch(candidate)
            if candidate_page:
                block.append(candidate)
                index += 1
                continue
            if (
                not candidate
                or _HEADING_RE.match(candidate)
                or _ITEM_RE.match(candidate)
                or _TABLE_LINE_RE.match(candidate)
            ):
                break
            block.append(candidate)
            index += 1
        units.append(
            (f"{current_page}\n{chr(10).join(block)}".strip(), False, section)
        )

    return units


def chunk_by_parent_child(
    text: str,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[ParentChildChunk]:
    """整表/整条目保留为 parent，过长时拆 child，child 仍携带完整 parent。"""

    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars 必须大于等于 0 且小于 max_chars")

    chunks: list[ParentChildChunk] = []
    for parent_index, (parent_text, atomic, section) in enumerate(
        _parent_child_units(text)
    ):
        parent_text = parent_text.strip()
        if not parent_text:
            continue
        children = (
            [parent_text]
            if len(parent_text) <= max_chars
            else chunk_by_paragraph(
                parent_text,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
        for child in children:
            chunks.append(
                ParentChildChunk(
                    parent_id=f"parent-{parent_index}",
                    parent_text=parent_text,
                    child_text=child,
                    section=section,
                    atomic_parent=atomic,
                )
            )
    return chunks


def chunk_by_semantic(
    text: str,
    max_chars: int = 1200,
    overlap_chars: int = 120,
) -> list[str]:
    """语义切片档位占位：当前未启用，调用方必须显式处理。"""

    raise NotImplementedError("semantic chunker 未启用")
