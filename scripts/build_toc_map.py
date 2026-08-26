# -*- coding: utf-8 -*-
"""T2-P2：扫描 kb/ 下全部 PDF 的内嵌书签目录，生成 data/toc_map.json。
格式：{ 文件名: [ {"t": 标题, "s": 起始页, "e": 结束页} ] }（页码为 1-based）。
无书签的 PDF 不写入（检索时保持旧的页码显示）。只读 PDF、写一个 json。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "toc_map.json"

toc_map = {}
pdfs = sorted(ROOT.glob("kb/**/*.pdf"))
for pdf in pdfs:
    try:
        doc = fitz.open(pdf)
        toc = doc.get_toc(simple=True)  # [[level, title, page_1based], ...]
        doc.close()
    except Exception as exc:
        print("SKIP", pdf.name, repr(exc)[:80])
        continue
    if not toc:
        print("NO-TOC", pdf.name)
        continue
    entries = []
    for i, (level, title, page) in enumerate(toc):
        end = toc[i + 1][2] - 1 if i + 1 < len(toc) else page  # 同级下一项前一夜；粗粒度即可
        entries.append({"t": title.strip(), "s": int(page), "e": max(int(page), int(end))})
    toc_map[pdf.name] = entries
    print("OK", pdf.name, f"{len(entries)} 条书签")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(toc_map, ensure_ascii=False, indent=1), encoding="utf-8")
print("WROTE", OUT, "覆盖", len(toc_map), "/", len(pdfs), "个 PDF")