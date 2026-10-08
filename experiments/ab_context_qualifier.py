"""语境判定 vs 裸子串匹配的误报/检出对照（确定性，不调 LLM）。

既有 ab_allergen_guardrail.py 证明了「关护栏漏、开护栏拦」；本实验补另一个
维度：护栏判定**不是**关键词 grep——`_has_unqualified_occurrence` 带前缀语境
（不吃/不放/避免/少盐…→ 不算命中）与病种否定（「没有痛风」→ 不触发）。

三组样本：
  safe_qualifier  安全文本（限定词/否定语境），期望两组都不误报才对——
                  但裸子串会误报，语境判定应为 0（这正是要证明的差异）。
  positive        真实违禁（阳性对照）：语境判定不能因为加了限定词逻辑而
                  牺牲检出，两组都必须检出，否则判定被改松了。
  condition_neg    病种否定句：detect_conditions 应不触发该病种。

用法：
    .venv\\Scripts\\python.exe experiments\\ab_context_qualifier.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.nutrition_rules import (
    RULES,
    audit,
    detect_conditions,
    _has_unqualified_occurrence,  # 语境判定的最小单元（带下划线，实验直接对照其语义）
)

OUTPUT = Path(__file__).with_name("ab_context_qualifier_result.csv")

# (case_id, condition, text, expect_detect)  expect_detect: 期望语境判定的检出数（0/1）
CASES = [
    # —— 安全文本：限定词在关键词前，语境判定应放行 ——
    ("safe-no-salt", "高血压", "这道菜不放盐，用柠檬汁提味", 0),
    ("safe-no-alcohol", "孕期", "本餐不含酒精，以鲜榨果汁代替", 0),
    ("safe-avoid-fried", "高脂血症", "全程避免油炸，以清蒸为主", 0),
    ("safe-no-heavy-soup", "痛风", "不喝浓汤，多喝白开水", 0),
    ("safe-less-sugar", "糖尿病", "不加糖，用代糖替代", 0),
    ("safe-no-organ", "痛风", "不吃动物内脏，选瘦肉", 0),
    ("safe-refuse-drink", "高血压", "拒绝饮酒，任何酒都不喝", 0),
    ("safe-no-pickle", "高血压", "不吃咸菜，配新鲜蔬菜", 0),
    # —— 阳性对照：真实违禁，语境判定必须照常检出 ——
    ("pos-pickle", "高血压", "配一碟咸菜更下饭", 1),
    ("pos-beer", "痛风", "晚餐配一瓶冰啤酒", 1),
    ("pos-raw-fish", "孕期", "刺身拼盘配三文鱼生鱼片", 1),
    ("pos-fried", "高脂血症", "油炸食品更香", 1),
    ("pos-organ-soup", "痛风", "老火汤炖猪肝最补", 1),
    ("pos-honey", "糖尿病", "蜂蜜甜饮一杯", 1),
]

CONDITION_NEGATION_CASES = [
    # (case_id, text, 不应触发的 condition)
    ("neg-no-gout", "没有痛风的人喝啤酒没问题吧", "痛风"),
    ("neg-not-pregnant", "不是孕妇也能吃沙拉", "孕期"),
    ("neg-no-diabetes", "他不是糖尿病患者", "糖尿病"),
]


def naked_hit(text: str, keyword: str) -> bool:
    """对照组：裸子串匹配（无语境判定）。"""
    return keyword in (text or "")


def main() -> int:
    rows = []
    for case_id, cond, text, expect in CASES:
        forbidden = RULES[cond]["forbidden"]
        naked = sum(1 for kw in set(forbidden) if naked_hit(text, kw))
        # 语境判定口径与生产 audit 一致：mode=forbidden 走 _has_unqualified_occurrence
        contextual = sum(
            1 for kw in set(forbidden)
            if _has_unqualified_occurrence(text, kw, "forbidden")
        )
        # 生产函数直算一遍（等价性绊线：上面的逐词口径必须与 audit() 一致）
        audit_n = len(audit(text, [cond]))
        # 安全组期望恰好 0（误报口径）；阳性组期望「至少 1」（存在性口径，
        # 「啤酒」含内部「酒」双命中是产品真实行为，不算错）。
        if expect == 0:
            expected_pass = contextual == 0
        else:
            expected_pass = contextual >= expect
        expected_pass = expected_pass and (naked >= contextual)
        rows.append({
            "case_id": case_id, "group": "safe_qualifier" if expect == 0 else "positive",
            "condition": cond, "text": text,
            "naked_hits": naked, "contextual_hits": contextual,
            "audit_hits": audit_n, "expect_contextual": expect,
            "pass": expected_pass and audit_n >= contextual,
        })

    for case_id, text, not_expect in CONDITION_NEGATION_CASES:
        detected = detect_conditions(text)
        rows.append({
            "case_id": case_id, "group": "condition_negation", "condition": not_expect,
            "text": text, "naked_hits": "-", "contextual_hits": "-",
            "audit_hits": "-", "expect_contextual": "not_triggered",
            "pass": not_expect not in detected,
        })

    with OUTPUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    safe = [r for r in rows if r["group"] == "safe_qualifier"]
    pos = [r for r in rows if r["group"] == "positive"]
    neg = [r for r in rows if r["group"] == "condition_negation"]
    naked_fp = sum(r["naked_hits"] > 0 for r in safe)
    ctx_fp = sum(r["contextual_hits"] > 0 for r in safe)
    naked_tp = sum(r["naked_hits"] > 0 for r in pos)
    ctx_tp = sum(r["contextual_hits"] > 0 for r in pos)
    neg_ok = sum(bool(r["pass"]) for r in neg)
    total_pass = sum(bool(r["pass"]) for r in rows)

    print(f"安全文本 {len(safe)} 例：裸子串误报 {naked_fp}，语境判定误报 {ctx_fp}")
    print(f"阳性对照 {len(pos)} 例：裸子串检出 {naked_tp}，语境判定检出 {ctx_tp}")
    print(f"病种否定 {len(neg)} 例：detect 正确不触发 {neg_ok}")
    print(f"总计 {len(rows)} 例，pass {total_pass}")
    print(f"结果已写入 {OUTPUT.name}")
    return 0 if total_pass == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
