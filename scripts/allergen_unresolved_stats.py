#!/usr/bin/env python3
"""统计过敏原未归一日志，按出现频次输出待补别名。"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / "data" / "allergen_unresolved.log"


def summarize_unresolved_log(
    path: Path,
    limit: int | None = None,
) -> list[tuple[str, int]]:
    """读取 ``时间\t原文`` 日志，返回按频次和原文排序的统计结果。"""
    counter: Counter[str] = Counter()
    try:
        lines: Iterable[str] = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        value = raw.split("\t", 1)[1].strip() if "\t" in raw else raw
        if value:
            counter[value] += 1

    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None and limit > 0:
        return ranked[:limit]
    return ranked


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计 data/allergen_unresolved.log 中未归一的过敏原表达。",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="日志路径（默认 data/allergen_unresolved.log）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只输出频次最高的前 N 项",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        rows = summarize_unresolved_log(args.log, limit=args.limit)
    except OSError as exc:
        print(f"读取日志失败：{exc}", file=sys.stderr)
        return 1

    if not rows:
        print("暂无未归一过敏原记录。")
        return 0

    for expression, count in rows:
        print(f"{count}\t{expression}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
