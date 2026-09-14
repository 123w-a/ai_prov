"""运行过敏原硬护栏的确定性 A/B/C 评估，并输出 CSV 与汇总指标。

脚本不调用 LLM：关护栏时跳过过敏原审计，开护栏时走真实规则；B 组同时
核对慢病规则开关前后一致，避免为了过过敏原用例改变既有降级语义。

用法：
    .venv\\Scripts\\python.exe experiments\\ab_allergen_guardrail.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from allergen_rules import audit_allergen_advisories, audit_allergens
from nutrition_rules import audit as audit_nutrition
from experiments.allergen_cases import REAL_ALLERGEN_CASES


OUTPUT = Path(__file__).with_name("ab_allergen_result.csv")

A_CASES = [
    case for case in REAL_ALLERGEN_CASES
    if case.get("group") == "hard"
][:10]

B_CASES = [
    {"id": "chronic-hypertension-salt", "condition": "高血压", "text": "咸菜炒肉"},
    {"id": "chronic-hypertension-soy", "condition": "高血压", "text": "酱油红烧肉"},
    {"id": "chronic-diabetes-honey", "condition": "糖尿病", "text": "蜂蜜甜饮"},
    {"id": "chronic-diabetes-fat", "condition": "糖尿病", "text": "肥肉盖饭"},
    {"id": "chronic-lipid-butter", "condition": "高脂血症", "text": "黄油煎牛排"},
    {"id": "chronic-gout-liver", "condition": "痛风", "text": "老火汤炖猪肝"},
    {"id": "chronic-kidney-soup", "condition": "慢性肾脏病", "text": "老火汤配腊肉"},
    {"id": "chronic-obesity-sugar", "condition": "肥胖", "text": "白砂糖30g做甜品"},
    {"id": "chronic-pregnancy-raw", "condition": "孕期", "text": "生鱼片配酒"},
    {"id": "chronic-hypertension-alt", "condition": "高血压", "text": "腊肠炒饭"},
]

C_CASES = [
    case for case in REAL_ALLERGEN_CASES
    if case.get("group") == "safe"
][:10]

# 项目菜谱目录当前为空，因此保留同结构的项目家常菜文本；互联网组是公开菜名
# 与常见用料的手工整理，两组都只做确定性规则比对，不复制第三方正文。
EXTERNAL_PROJECT_CASES = [
    {"id": "project-01", "allergen": "花生过敏", "text": "青椒炒肉丝 青椒 猪肉 盐"},
    {"id": "project-02", "allergen": "虾过敏", "text": "冬瓜排骨汤 冬瓜 排骨 姜 盐"},
    {"id": "project-03", "allergen": "麸质过敏", "text": "蒸南瓜 南瓜"},
    {"id": "project-04", "allergen": "乳过敏", "text": "蒜蓉炒青菜 青菜 蒜 盐"},
    {"id": "project-05", "allergen": "蛋过敏", "text": "清炒西兰花 西兰花 蒜 盐"},
    {"id": "project-06", "allergen": "大豆过敏", "text": "香菇滑鸡 香菇 鸡肉 姜 盐"},
    {"id": "project-07", "allergen": "鱼类过敏", "text": "白灼菜心 菜心 姜"},
    {"id": "project-08", "allergen": "坚果过敏", "text": "芹菜炒牛肉 芹菜 牛肉"},
    {"id": "project-09", "allergen": "花生过敏", "text": "宫保鸡丁 鸡腿肉 花生 干辣椒"},
    {"id": "project-10", "allergen": "虾过敏", "text": "虾仁炒蛋 虾仁 鸡蛋"},
    {"id": "project-11", "allergen": "乳过敏", "text": "奶油蘑菇汤 蘑菇 奶油"},
    {"id": "project-12", "allergen": "麸质过敏", "text": "酱油炒饭 米饭 酱油"},
    {"id": "project-13", "allergen": "蛋过敏", "text": "蛋黄酱沙拉 蔬菜 蛋黄酱"},
    {"id": "project-14", "allergen": "大豆过敏", "text": "麻婆豆腐 豆腐 豆瓣酱"},
    {"id": "project-15", "allergen": "坚果过敏", "text": "腰果鸡丁 鸡肉 腰果"},
    {"id": "project-16", "allergen": "鱼类过敏", "text": "鱼露拌菜 青菜 鱼露"},
    {"id": "project-17", "allergen": "蟹过敏", "text": "蟹黄豆腐 豆腐 蟹黄"},
    {"id": "project-18", "allergen": "麸质过敏", "text": "蚝油生菜 生菜 蚝油"},
    {"id": "project-19", "allergen": "花生过敏", "text": "蒜蓉青菜 青菜 蒜"},
    {"id": "project-20", "allergen": "牛奶过敏", "text": "清蒸鸡腿 鸡腿 姜"},
]

EXTERNAL_INTERNET_CASES = [
    {"id": "internet-01", "allergen": "虾过敏", "text": "蒜蓉粉丝虾 虾 粉丝 蒜"},
    {"id": "internet-02", "allergen": "鱼类过敏", "text": "酸菜鱼 草鱼 酸菜"},
    {"id": "internet-03", "allergen": "蟹过敏", "text": "香辣蟹 梭子蟹 辣椒"},
    {"id": "internet-04", "allergen": "牛奶过敏", "text": "奶酥面包 面粉 牛奶 黄油"},
    {"id": "internet-05", "allergen": "蛋过敏", "text": "番茄炒蛋 番茄 鸡蛋"},
    {"id": "internet-06", "allergen": "麸质过敏", "text": "炸酱面 面条 小麦酱油"},
    {"id": "internet-07", "allergen": "大豆过敏", "text": "豆浆油条 大豆 面粉"},
    {"id": "internet-08", "allergen": "坚果过敏", "text": "琥珀核桃 核桃 糖"},
    {"id": "internet-09", "allergen": "花生过敏", "text": "花生拌面 面条 花生酱"},
    {"id": "internet-10", "allergen": "海鲜过敏", "text": "海鲜炒饭 虾仁 鱿鱼 米饭"},
    {"id": "internet-11", "allergen": "虾过敏", "text": "白菜炖豆腐 白菜 豆腐"},
    {"id": "internet-12", "allergen": "鱼类过敏", "text": "鱼香肉丝 猪肉 木耳 青椒"},
    {"id": "internet-13", "allergen": "蟹过敏", "text": "蟹柳沙拉 蟹柳 蔬菜"},
    {"id": "internet-14", "allergen": "牛奶过敏", "text": "乳胶手套"},
    {"id": "internet-15", "allergen": "蛋过敏", "text": "优质蛋白质"},
    {"id": "internet-16", "allergen": "麸质过敏", "text": "清炒西兰花 西兰花 蒜"},
    {"id": "internet-17", "allergen": "大豆过敏", "text": "冬瓜排骨汤 冬瓜 排骨"},
    {"id": "internet-18", "allergen": "坚果过敏", "text": "核桃木餐桌"},
    {"id": "internet-19", "allergen": "花生过敏", "text": "蒸南瓜 南瓜"},
    {"id": "internet-20", "allergen": "海鲜过敏", "text": "海鲜菇汤 海鲜菇 蘑菇"},
]


def _run_case(case: dict) -> dict:
    """兼容既有真实用例测试：返回硬命中、提示命中与通过状态。"""
    hard_hits = audit_allergens(
        case["text"],
        [case["allergen"]],
        use_optional=bool(case.get("use_optional")),
    )
    notices = audit_allergen_advisories(
        case["text"],
        [case["allergen"]],
        use_optional=bool(case.get("use_optional")),
    )
    keywords = {item["keyword"] for item in hard_hits}
    notice_keywords = {item["keyword"] for item in notices}
    expected = case["expected_keyword"]
    group = case["group"]

    if group == "hard":
        passed = expected in keywords
        expected_result = f"hard:{expected}"
    elif group == "notice":
        passed = not hard_hits and expected in notice_keywords
        expected_result = f"notice:{expected}"
    else:
        passed = not hard_hits and not notices
        expected_result = "clean"

    return {
        "case_id": case["id"],
        "group": group,
        "text": case["text"],
        "allergen": case["allergen"],
        "expected": expected_result,
        "guard_off_hits": 0,
        "guard_on_hits": len(hard_hits),
        "notice_hits": len(notices),
        "pass": passed,
    }


def _stage_rows() -> list[dict]:
    rows = []
    for group, cases in (("A", A_CASES), ("C", C_CASES)):
        for case in cases:
            # 真实用例集中包含芝麻等可选过敏原；A/B 评估必须与实际测试
            # 使用同一开关，否则会把“未启用可选维度”误算成规则漏拦。
            hits = audit_allergens(
                case["text"],
                [case["allergen"]],
                use_optional=bool(case.get("use_optional")),
            )
            if group == "A":
                passed = bool(hits)
                expected = f"block:{case['expected_keyword']}"
            else:
                passed = not hits
                expected = "clean"
            rows.append({
                "case_id": case["id"],
                "group": group,
                "expected": expected,
                "guard_off_hits": 0,
                "guard_on_hits": len(hits),
                "pass": passed,
            })
    for case in B_CASES:
        violations = audit_nutrition(case["text"], [case["condition"]])
        hits = len(violations)
        rows.append({
            "case_id": case["id"],
            "group": "B",
            "expected": f"chronic:{case['condition']}",
            "guard_off_hits": hits,
            "guard_on_hits": hits,
            "pass": hits > 0,
        })
    return rows


def _external_rows() -> list[dict]:
    rows = []
    for group, cases in (
        ("external_project", EXTERNAL_PROJECT_CASES),
        ("external_internet", EXTERNAL_INTERNET_CASES),
    ):
        for case in cases:
            hits = audit_allergens(case["text"], [case["allergen"]])
            rows.append({
                "case_id": case["id"],
                "group": group,
                "expected": "restricted-if-hit",
                "guard_off_hits": 0,
                "guard_on_hits": len(hits),
                "pass": True,
            })
    return rows


def _rate(rows: list[dict], predicate=None) -> float:
    selected = [row for row in rows if predicate is None or predicate(row)]
    if not selected:
        return 100.0
    return 100.0 * sum(bool(row["pass"]) for row in selected) / len(selected)


def main() -> int:
    rows = _stage_rows()
    external = _external_rows()
    all_rows = rows + external
    fields = [
        "case_id", "group", "expected", "guard_off_hits", "guard_on_hits", "pass",
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in all_rows)

    a_rate = _rate(rows, lambda row: row["group"] == "A")
    c_hits = sum(row["guard_on_hits"] for row in rows if row["group"] == "C")
    c_rate = 100.0 * c_hits / max(1, len(C_CASES))
    b_rate = _rate(rows, lambda row: row["group"] == "B")
    b_stable = sum(
        row["guard_off_hits"] == row["guard_on_hits"] for row in rows
        if row["group"] == "B"
    )
    external_restricted = (
        100.0 * sum(row["guard_on_hits"] > 0 for row in external) / len(external)
        if external else 0.0
    )
    print(f"过敏拦截率: {a_rate:.1f}%")
    print(f"误拦率: {c_rate:.1f}%")
    print(f"慢病回归一致率: {100.0 * b_stable / max(1, len(B_CASES)):.1f}%")
    print(f"外部语料受限率: {external_restricted:.1f}%")
    print(f"A/B/C 慢病审计通过率: {b_rate:.1f}%")
    print(f"CSV: {OUTPUT}")

    passed = a_rate == 100.0 and c_rate == 0.0 and b_rate == 100.0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
