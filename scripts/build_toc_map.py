# -*- coding: utf-8 -*-
"""T2-P2：扫描 kb/ 下全部 PDF 的内嵌书签目录，生成 data/toc_map.json。
格式：{ 文件名: [ {"t": 标题, "s": 起始页, "e": 结束页} ] }（页码为 1-based）。
无书签的 PDF 不写入（检索时保持旧的页码显示）。只读 PDF、写一个 json。"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "toc_map.json"

toc_map = {}

_TOC_LINE = re.compile(r"^(.{2,60}?)[\s.…·]{2,}(\d{1,3})\s*$")


def _text_toc(doc):
    """无书签 PDF：扫前 8 页找「标题……页码」行，正文定位校准印刷页偏移。"""
    raw = []
    for pno in range(min(8, doc.page_count)):
        for line in doc[pno].get_text().splitlines():
            m = _TOC_LINE.match(line.strip())
            if not m:
                continue
            t = re.sub(r"\s+", "", m.group(1))
            if len(t) >= 4 and t not in {"目录", "目录 contents"}:
                raw.append((t, int(m.group(2))))
    if len(raw) < 4:
        return []
    offset = 0
    for title, page in raw[:6]:
        want = title[:6]
        lo, hi = max(1, page - 12), min(doc.page_count, page + 12)
        hit = next((p for p in range(lo, hi + 1) if want in doc[p - 1].get_text()), None)
        if hit:
            offset = hit - page
            break
    out, last = [], None
    for i, (title, page) in enumerate(raw):
        s = max(1, page + offset)
        e = max(s, (raw[i + 1][1] + offset - 1) if i + 1 < len(raw) else s)
        if last and s <= last["e"]:
            last["e"] = max(last["e"], e)
            continue
        item = {"t": title[:30], "s": s, "e": e}
        out.append(item)
        last = item
    return out

pdfs = sorted(ROOT.glob("kb/**/*.pdf"))
for pdf in pdfs:
    try:
        doc = fitz.open(pdf)
        toc = doc.get_toc(simple=True)  # [[level, title, page_1based], ...]
    except Exception as exc:
        print("SKIP", pdf.name, repr(exc)[:80])
        continue
    if not toc:
        entries = _text_toc(doc)
        doc.close()
        if entries:
            toc_map[pdf.name] = entries
            print("TEXT-TOC", pdf.name, f"{len(entries)} 条（目录页解析）")
        else:
            print("NO-TOC", pdf.name)
        continue
    doc.close()
    entries = []
    for i, (level, title, page) in enumerate(toc):
        end = toc[i + 1][2] - 1 if i + 1 < len(toc) else page  # 同级下一项前一夜；粗粒度即可
        entries.append({"t": title.strip(), "s": int(page), "e": max(int(page), int(end))})
    toc_map[pdf.name] = entries
    print("OK", pdf.name, f"{len(entries)} 条书签")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(toc_map, ensure_ascii=False, indent=1), encoding="utf-8")
print("WROTE", OUT, "覆盖", len(toc_map), "/", len(pdfs), "个 PDF")