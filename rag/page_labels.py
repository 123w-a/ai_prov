"""PDF 印刷页码映射：把索引里的物理页码换算成文档自身印的页码。

背景：``rag.ingest`` 写进 metadata 的 ``_pN`` 锚点用的是解析层带回来的物理页序号
（PDF 的第几页）。用户核对来源时看的是文档页眉/页脚上印的页码，两者在多数国标/
指南里差一个固定偏移（封面、目录、前言不计入正文页码）。

数据来源：对 ``kb/**/*.pdf`` 做只读扫描——每页取首/尾各 3 行里"独立成行的整数"
作为印刷页码候选，只统计"整页只出现一个候选值"的页，取众数偏移；当且仅当该偏移
覆盖了 ≥60% 的可用页、且领先次高值一倍以上时才收录。少数 PDF 无法给出单一稳定
偏移，例如 2-up 拼版扫描的《中国居民膳食指南（2022）》节选（一页含两个印刷页）、
只有封面目录没有页码的附件类文件，这里一律不收录，退回物理页码显示。

偏移含义：``offset = 物理页 - 印刷页``，即 ``印刷页 = 物理页 - offset``。
国标类 PDF 的附录页码会重新起编，本表只能保证正文区间准确。
KB 内的 PDF 被替换后需要重新核对本表。
"""

from __future__ import annotations

# 键与 RetrievalHit.source / chunk metadata["source"] 一致，均为 path.name。
PDF_PAGE_OFFSETS: dict[str, int] = {
    # 慢病食养指南：正文印刷页码从物理第 3 页起算。
    "成人高血压食养指南（2023年版）.pdf": 2,
    "成人糖尿病食养指南（2023年版）.pdf": 2,
    "成人高脂血症食养指南（2023年版）.pdf": 2,
    "成人肥胖食养指南（2024年版）.pdf": 2,
    "成人高尿酸血症与痛风食养指南（2024年版）.pdf": 2,
    "成人慢性肾脏病食养指南（2024年版）.pdf": 2,
    # WS/T 578 系列营养素参考摄入量。
    "WS_T_578.1_宏量营养素.pdf": 1,
    "WS_T_578.2_常量元素.pdf": 1,
    "WS_T_578.3_微量元素.pdf": 2,
    "WS_T_578.4_脂溶性维生素.pdf": 1,
    "WS_T_578.5_水溶性维生素.pdf": 2,
    # 食品安全国家标准：正文偏移稳定，附录另行起编。
    "GB 7718-2025 预包装食品标签通则.pdf": 2,
    "GB 28050-2025 预包装食品营养标签通则.pdf": 2,
    # 特殊人群与地方标准。
    "老年人膳食指导 WS-T 556-2017.pdf": 2,
    "学生餐营养指南 WS-T 554-2017.pdf": 2,
    "婴幼儿喂养与膳食规范_深圳DB4403_1479172.pdf": 4,
    "膳食营养规范_深圳DB4403_1540797.pdf": 4,
}


def printed_page(source: str, physical_page: int) -> int | None:
    """返回 ``source`` 文档中 ``physical_page`` 对应的印刷页码。

    返回 ``None`` 表示该文档没有可靠映射（未收录、页码越界或入参非法），
    调用方应保留原有物理页码，不要臆造页码。
    """
    offset = PDF_PAGE_OFFSETS.get(str(source or "").strip())
    if offset is None:
        return None
    try:
        physical = int(physical_page)
    except (TypeError, ValueError):
        return None
    printed = physical - offset
    if printed < 1:
        return None
    return printed
