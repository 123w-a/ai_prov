"""切片策略路由：结构特征决定切法，LLM 不参与选择。"""

from __future__ import annotations

import re
from typing import Any


STRATEGIES = (
    "heading_parent",
    "paragraph",
    "parent_child",
    "semantic",
    "proposition",
)

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


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def probe_features(text: str, doc_meta: dict[str, Any]) -> dict[str, Any]:
    """探测标题、表格、条目和文档长度等确定性结构特征。"""

    lines = _nonempty_lines(text)
    total = max(len(lines), 1)
    heading_count = sum(bool(_HEADING_RE.match(line)) for line in lines)
    item_count = sum(bool(_ITEM_RE.match(line)) for line in lines)
    table_count = sum(bool(_TABLE_LINE_RE.match(line)) for line in lines)
    content_type = str((doc_meta or {}).get("content_type") or "")
    return {
        "has_headings": heading_count > 0,
        "heading_count": heading_count,
        "item_density": round(item_count / total, 4),
        "item_count": item_count,
        "table_density": round(table_count / total, 4),
        "table_count": table_count,
        "length": len(text or ""),
        "content_type": content_type,
    }


def choose_chunker(text: str, doc_meta: dict[str, Any]) -> tuple[str, str]:
    """返回 ``(strategy, reason)``；reason 会写入 chunk metadata。"""

    features = probe_features(text, doc_meta)
    content_type = features["content_type"]
    category = str((doc_meta or {}).get("category") or "")

    if content_type == "markdown" and features["has_headings"]:
        return (
            "heading_parent",
            f"Markdown 含 {features['heading_count']} 个清晰标题，按标题段保持父级结构",
        )

    if category == "1_慢病食养指南":
        return (
            "parent_child",
            "慢病食养指南以表格和条目为主，完整知识单元作为 parent，片段作为 child 检索",
        )

    if features["table_density"] >= 0.08 and features["item_density"] >= 0.12:
        return (
            "parent_child",
            "检测到表格/条目密集，使用 parent-child 避免表格和条目被腰斩",
        )

    if features["length"] >= 18000 and features["heading_count"] <= 2:
        # semantic 档位先保留，未启用时不静默冒充语义切片。
        return (
            "paragraph",
            f"长文 {features['length']} 字但语义切片未启用，保守使用段落切片",
        )

    return (
        "paragraph",
        f"文档结构较普通（{features['length']} 字），使用稳定的段落切片",
    )
