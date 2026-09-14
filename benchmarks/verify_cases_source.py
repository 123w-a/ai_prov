"""核验评测用例的 expect_keywords 确实能在原文里找到（防"答案来自检索结果"的循环论证）。

用法：
    .venv\\Scripts\\python.exe benchmarks\\verify_cases_source.py

判定：每条用例至少 1 个关键词出现在 source_doc 原文中才算合格。
      不合格的用例会让 recall 指标失去意义（标准答案根本不在库里），
      因此这里返回非 0 退出码，方便接进 CI / 自检清单。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.retrieval_cases_v2 import RETRIEVAL_CASES_V2  # noqa: E402

KB_DIR = ROOT / "kb"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _load_doc_text(doc_name: str, cache: dict) -> str | None:
    if doc_name in cache:
        return cache[doc_name]
    matches = list(KB_DIR.rglob(doc_name))
    if not matches:
        cache[doc_name] = None
        return None
    try:
        import pdfplumber
    except ImportError:
        cache[doc_name] = None
        return None
    with pdfplumber.open(matches[0]) as pdf:
        text = _norm("\n".join(page.extract_text() or "" for page in pdf.pages))
    cache[doc_name] = text
    return text


def main() -> int:
    cache: dict = {}
    bad = []
    for case in RETRIEVAL_CASES_V2:
        doc_text = _load_doc_text(case["source_doc"], cache)
        if doc_text is None:
            bad.append((case["id"], case["source_doc"], "文档未找到或无法解析"))
            print(f"  ❌ {case['id']}: 源文档不可用 -> {case['source_doc']}")
            continue
        hit = [kw for kw in case["expect_keywords"] if _norm(kw) in doc_text]
        if hit:
            print(f"  ✅ {case['id']}: 命中 {hit}")
        else:
            bad.append((case["id"], case["source_doc"], "关键词全部不在原文"))
            print(f"  ❌ {case['id']}: 关键词 {case['expect_keywords']} 均不在原文")

    total = len(RETRIEVAL_CASES_V2)
    print(f"\n合格 {total - len(bad)}/{total} 条")
    if bad:
        print("不合格明细：")
        for case_id, doc, reason in bad:
            print(f"  - {case_id} | {doc} | {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
