"""老餐次的成员归属回填（CLI 工具）。

背景：`data/meals.json` 早期的记录只有 ts/session/dish/lights/guardrails，
**没有成员归属**，于是"某成员本周的达标餐次 ÷ 已记录餐次"这个分母算不出来
（见 api/routes/reports_route.py 的 member_nutrition_progress）。

本工具按 2026-10-11 用户裁定的口径做 best-effort 回填：
  · **只有 session + dish 唯一匹配到会话 answer 时才写** `member`；
  · 多解 / 匹配不上 / 命中那条自己也没记 primary_member → 留空 = **未归属**，
    不计入任何成员、绝不摊派；
  · 只新增 `member` 一个键，ts/session/dish/lights/guardrails 一字不动；
  · 幂等：已有 member 的记录一律跳过，重复运行结果相同。

用法：
  python scripts/backfill_meal_members.py            # dry-run：只统计不落盘
  python scripts/backfill_meal_members.py --apply    # 真正回写 data/meals.json

每次运行都打印可审计计数：回填成功 N / 跳过 M（已有归属）/ 未归属 K
（K = 多解 + 匹配不上 + 命中无成员）。同一组数也可以随时从磁盘复算：
  GET /reports/meal-attribution
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.meal_attribution import MEALS_PATH, backfill_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="老餐次成员归属回填（session+dish 唯一匹配）")
    parser.add_argument("--apply", action="store_true", help="真正回写 meals.json（缺省 dry-run）")
    parser.add_argument("--meals", default=str(MEALS_PATH), help=f"记账文件（缺省 {MEALS_PATH}）")
    args = parser.parse_args()

    report = backfill_report(
        meals_path=Path(args.meals),
        dry_run=not args.apply,
    )
    print(f"记账文件：{report['meals_path']}")
    print(f"会话扫描：{report['sessions_scanned']} 个会话有结构化答案")
    print(f"总记录：{report['total']} 条")
    print(f"回填成功 N = {report['filled']} 条")
    print(f"跳过 M     = {report['already']} 条（已有 member，幂等）")
    print(
        f"未归属 K   = {report['unattributed']} 条"
        f"（多解 {report['ambiguous']} / 匹配不上 {report['unmatched']} / 命中无成员 {report['no_member']}）"
    )
    if report["dry_run"]:
        print("（dry-run，未落盘；加 --apply 执行回写）")
    else:
        print(f"已回写：新增 member 键 {report['filled']} 条，其余键未改动")


if __name__ == "__main__":
    main()
