# constraint_rules.py：统一约束引擎（L3）
# dimension 说明：
#   allergen 安全·过敏原   -> 激活成员硬审计；其他成员进入分餐矩阵
#   chronic  健康·慢病忌口 -> 换菜·硬审计（只按激活成员）
#   texture  口感·质地     -> 不换菜·调做法（输出层）
#   energy   目标·能量体重 -> 不换菜·调份量（输出层）
# 统一条目（采集端与审计端共用）：
#   {"member","dimension","value","severity","scope","source_text","status"}
#   severity: hard=硬约束(过敏/医嘱) / soft=偏好
#   scope: always=长期 / once=仅本次

from __future__ import annotations

from typing import Any, Iterable


CONSTRAINT_DIMENSIONS = ("allergen", "chronic", "restrict", "texture", "energy")

TEXTURE_RULES = {
    "软食": {
        "avoid": ["坚果", "花生", "脆骨", "油炸", "煎", "牛肉干", "生萝卜"],
        "advice": "切小块/剁细，蒸煮延长 5-10 分钟，优先南瓜/冬瓜/豆腐等易软食材",
    },
}

ENERGY_RULES = {
    "减重": {
        "advice": "主食减半、蔬菜加量、不额外淋油；保留优质蛋白质",
    },
    "增重": {
        "advice": "主食和优质蛋白质分批加量，避免用含糖饮料补热量",
    },
}

_ENERGY_WORDS = ("减重", "减肥", "控制体重", "增重", "增肌")
_TEXTURE_WORDS = ("软食", "咬不动", "咀嚼困难", "质地软", "软烂")
_PREFERENCE_PREFIXES = ("不能吃", "不吃", "忌口", "不要")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalise_entry(entry: Any) -> dict:
    """把字符串或统一条目转成稳定结构，兼容旧调用方只传规则键。"""
    if isinstance(entry, dict):
        item = dict(entry)
        item["dimension"] = _text(item.get("dimension"))
        item["value"] = _text(item.get("value"))
        item["member"] = _text(item.get("member"))
        item["severity"] = _text(item.get("severity")) or "hard"
        item["scope"] = _text(item.get("scope")) or "always"
        item["source_text"] = _text(item.get("source_text"))
        item["status"] = _text(item.get("status")) or "confirmed"
        return item

    value = _text(entry)
    dimension = ""
    try:
        from allergen_rules import normalize_allergens

        if normalize_allergens(value, log_unresolved=False):
            dimension = "allergen"
    except Exception:
        pass
    if not dimension:
        try:
            from nutrition_rules import RULES

            if value in RULES or any(word in value for word in RULES):
                dimension = "chronic"
        except Exception:
            pass
    if not dimension and any(word in value for word in _TEXTURE_WORDS):
        dimension = "texture"
    if not dimension and any(word in value for word in _ENERGY_WORDS):
        dimension = "energy"
    if not dimension and value.startswith(_PREFERENCE_PREFIXES):
        dimension = "preference"
    return {
        "member": "",
        "dimension": dimension,
        "value": value,
        "severity": "hard",
        "scope": "always",
        "source_text": "",
        "status": "confirmed",
    }


def _entry_rows(entries: Iterable[Any]) -> list[dict]:
    rows = []
    for entry in entries or []:
        row = _normalise_entry(entry)
        if row["value"]:
            rows.append(row)
    return rows


def entries_from_profile(profile: dict, member: str = "") -> list[dict]:
    """把结构化画像展开成统一约束条目，缺字段时返回空而不抛异常。"""
    profile = profile if isinstance(profile, dict) else {}
    rows: list[dict] = []

    def add(dimension: str, values: Any, severity: str = "hard") -> None:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return
        for value in values:
            text = _text(value)
            if text:
                rows.append({
                    "member": member,
                    "dimension": dimension,
                    "value": text,
                    "severity": severity,
                    "scope": "always",
                    "source_text": "",
                    "status": "confirmed",
                })

    add("allergen", profile.get("allergens"), "hard")
    add("chronic", profile.get("conditions"), "hard")
    add("restrict", profile.get("restricts"), "hard")
    add("preference", profile.get("dislikes"), "soft")
    texture_values = []
    for value in profile.get("taste_notes") or []:
        text = _text(value)
        if text and any(word in text for word in _TEXTURE_WORDS):
            texture_values.append(text)
    add("texture", texture_values, "soft")
    goal = _text(profile.get("goal"))
    if goal:
        add("energy", goal, "soft")
    return rows


def _allergen_codes(entries: list[dict]) -> list[str]:
    from allergen_rules import normalize_allergens

    codes = []
    for entry in entries:
        if entry["dimension"] != "allergen":
            continue
        for code in normalize_allergens(entry["value"]):
            if code not in codes:
                codes.append(code)
    return codes


def _chronic_values(entries: list[dict]) -> list[str]:
    from nutrition_rules import RULES

    values = []
    for entry in entries:
        if entry["dimension"] != "chronic":
            continue
        value = entry["value"]
        if value in RULES and value not in values:
            values.append(value)
        for rule in RULES:
            if rule in value and rule not in values:
                values.append(rule)
    return values


def _adjustment(entry: dict, advice: str, trigger: str = "") -> dict:
    return {
        "member": entry.get("member", ""),
        "dimension": entry["dimension"],
        "value": entry["value"],
        "severity": entry.get("severity", "soft"),
        "scope": entry.get("scope", "always"),
        "advice": advice,
        "trigger": trigger,
        "source_text": entry.get("source_text", ""),
        "status": entry.get("status", "confirmed"),
    }


def _texture_adjustments(text: str, entries: list[dict]) -> list[dict]:
    adjustments = []
    for entry in entries:
        if entry["dimension"] != "texture":
            continue
        rule = TEXTURE_RULES.get(entry["value"])
        if not rule:
            if any(word in entry["value"] for word in _TEXTURE_WORDS):
                rule = TEXTURE_RULES["软食"]
            else:
                continue
        hits = [word for word in rule["avoid"] if word in text]
        trigger = "、".join(hits) if hits else "做法调整"
        advice = rule["advice"]
        if hits:
            advice = f"避开{trigger}；" + advice
        adjustments.append(_adjustment(entry, advice, trigger))
    return adjustments


def _energy_adjustments(entries: list[dict]) -> list[dict]:
    adjustments = []
    for entry in entries:
        if entry["dimension"] != "energy":
            continue
        rule = ENERGY_RULES.get(entry["value"])
        if not rule:
            if any(word in entry["value"] for word in _ENERGY_WORDS):
                rule = ENERGY_RULES["减重"]
            else:
                continue
        adjustments.append(_adjustment(entry, rule["advice"], "份量调整"))
    return adjustments


def _preference_adjustments(text: str, entries: list[dict]) -> list[dict]:
    adjustments = []
    for entry in entries:
        if entry["dimension"] != "preference":
            continue
        value = entry["value"]
        target = value
        for prefix in _PREFERENCE_PREFIXES:
            if target.startswith(prefix):
                target = target[len(prefix):].strip()
                break
        if target and target in text:
            adjustments.append(
                _adjustment(entry, f"本轮避开{target}，用不辣/清淡做法替代", target)
            )
    return adjustments


def _restrict_violations(text: str, entries: list[dict]) -> list[dict]:
    """医嘱/长期硬限制：命中限制对象时按硬约束拦截，不降级成口味建议。"""
    violations = []
    for entry in entries:
        if entry["dimension"] != "restrict":
            continue
        value = entry["value"]
        target = value
        for prefix in _PREFERENCE_PREFIXES:
            if target.startswith(prefix):
                target = target[len(prefix):].strip()
                break
        if target and target in text:
            violations.append({
                "condition": "医嘱硬限制",
                "keyword": target,
                "message": f"{entry.get('member') or '成员'}需避免{target}",
                "dimension": "restrict",
                "source": entry.get("source_text", ""),
            })
    return violations


def audit_allergens_dimension(
    text: str,
    allergens: Iterable[Any],
    use_optional: bool = False,
) -> list[dict]:
    """过敏原统一分派入口；只转调既有确定性核心，避免复制词表。"""
    from allergen_rules import _audit_tier

    return _audit_tier(
        text,
        allergens,
        tier="hard",
        use_optional=use_optional,
    )


def audit_constraint(
    text: str,
    entries: list,
    use_optional: bool = True,
) -> tuple[list[dict], list[dict]]:
    """按 dimension 分派约束，硬维拦截、软维只产出可读调整建议。

    use_optional 默认 True：芝麻 / 软体动物 / 亚硫酸盐虽非中国八大类，
    但已建档就必须审，否则「建档了却不查」等于静默失效。
    """
    source_text = str(text or "")
    rows = _entry_rows(entries)
    allergen_codes = _allergen_codes(rows)
    chronic_values = _chronic_values(rows)

    violations = audit_allergens_dimension(
        source_text, allergen_codes, use_optional=use_optional
    )
    for violation in violations:
        violation["dimension"] = "allergen"
    for violation in __import__("nutrition_rules").audit(source_text, chronic_values):
        item = dict(violation)
        item["dimension"] = "chronic"
        violations.append(item)
    violations.extend(_restrict_violations(source_text, rows))

    adjustments = []
    adjustments.extend(_texture_adjustments(source_text, rows))
    adjustments.extend(_energy_adjustments(rows))
    adjustments.extend(_preference_adjustments(source_text, rows))

    # “可能含”只作为提醒，绝不升级成硬拦截。
    if allergen_codes:
        from allergen_rules import audit_allergen_advisories

        for notice in audit_allergen_advisories(
            source_text, allergen_codes, use_optional=use_optional
        ):
            item = dict(notice)
            item["advice"] = f"确认「{item.get('keyword', '')}」是否含致敏成分；不确定时不要使用"
            adjustments.append(item)

    # 未归一过敏原：没有规则可查，但必须让用户看见这个覆盖缺口。
    from allergen_rules import unresolved_allergen_advisories

    adjustments.extend(
        unresolved_allergen_advisories(
            [row["value"] for row in rows if row["dimension"] == "allergen"]
        )
    )
    return violations, adjustments


def build_matrix(
    dishes: list,
    members: list,
    use_optional: bool = True,
) -> list[dict]:
    """一桌菜 × 每位成员：不可吃 / 待确认 / 需调整 / 可吃，全部由规则确定性计算。

    「待确认」用于档案里存在**未归一过敏原**且该原文出现在菜名/食材里的情况：
    规则查不到，但也不能当「可吃」——那是对用户的虚假保证。
    """
    from allergen_rules import UNRESOLVED_ALLERGEN_ADVICE, unresolved_allergen_advisories

    result = []
    for dish in dishes or []:
        if isinstance(dish, dict):
            dish_text = " ".join(
                _text(dish.get(key))
                for key in ("name", "ingredients", "seasonings", "steps")
            )
            dish_name = _text(dish.get("name")) or dish_text
        else:
            dish_text = _text(dish)
            dish_name = dish_text
        for member in members or []:
            if not isinstance(member, dict):
                member = {"name": str(member), "profile": {}}
            member_name = _text(member.get("name")) or "成员"
            profile = member.get("profile") or {}
            rows = entries_from_profile(profile, member_name)
            violations, adjustments = audit_constraint(
                dish_text, rows, use_optional=use_optional
            )
            # 未归一过敏原只按「用户原文是否出现在这道菜里」判断，不猜同义词。
            unresolved_hits = [
                item["keyword"]
                for item in unresolved_allergen_advisories(profile.get("allergens"))
                if len(str(item.get("keyword") or "")) >= 2
                and str(item["keyword"]) in dish_text
            ]
            # 未归一提醒是「覆盖缺口」而不是「这道菜要调整」，不能拿它把成员标成需调整，
            # 否则每位未归一成员会在每道菜上都被挂一条无信息的结论。
            dish_adjustments = [
                item
                for item in adjustments
                if item.get("dimension") != "allergen_unresolved"
            ]
            if violations:
                has_allergen = any(
                    item.get("dimension") == "allergen" for item in violations
                )
                reasons = "、".join(
                    f"{item['condition']}忌{item['keyword']}" for item in violations
                )
                verdict = "不可吃" if has_allergen else "需调整"
                if has_allergen:
                    reason = f"{reasons}；需单独替换并避免交叉接触"
                else:
                    reason = f"{reasons}；可单独做低盐/低糖版本"
            elif unresolved_hits:
                verdict = "待确认"
                reason = (
                    "、".join(unresolved_hits)
                    + f"：{UNRESOLVED_ALLERGEN_ADVICE}；需单独替换并避免交叉接触"
                )
            elif dish_adjustments:
                verdict = "需调整"
                reason = "；".join(
                    dict.fromkeys(
                        item.get("advice", "")
                        for item in dish_adjustments
                        if item.get("advice")
                    )
                )
            else:
                verdict, reason = "可吃", ""
            result.append({
                "dish": dish_name,
                "member": member_name,
                "verdict": verdict,
                "reason": reason,
            })
    return result


def build_member_adjustments(matrix: list) -> list[str]:
    """把确定性矩阵收口成每位成员一句话，供卡片直接展示。"""
    grouped: dict[str, dict] = {}
    order: list[str] = []

    for row in matrix or []:
        if isinstance(row, dict):
            member = _text(row.get("member"))
            dish = _text(row.get("dish"))
            verdict = _text(row.get("verdict"))
            reason = _text(row.get("reason"))
        else:
            member = _text(getattr(row, "member", ""))
            dish = _text(getattr(row, "dish", ""))
            verdict = _text(getattr(row, "verdict", ""))
            reason = _text(getattr(row, "reason", ""))
        if not member:
            continue
        if member not in grouped:
            grouped[member] = {
                "blocked": [], "pending": [], "adjusted": [], "eatable": 0,
            }
            order.append(member)
        bucket = grouped[member]
        dish = dish or "共同主菜"
        if verdict == "不可吃":
            bucket["blocked"].append((dish, reason))
        elif verdict == "待确认":
            bucket["pending"].append((dish, reason))
        elif verdict == "需调整":
            bucket["adjusted"].append((dish, reason))
        else:
            bucket["eatable"] += 1

    adjustments = []
    for member in order:
        bucket = grouped[member]
        parts = []
        blocked = bucket["blocked"]
        pending = bucket["pending"]
        adjusted = bucket["adjusted"]
        if blocked:
            lines = []
            for dish, reason in blocked:
                line = f"「{dish}」不可吃，单独替换"
                if reason:
                    line += f"（{reason}）"
                if "交叉接触" not in line:
                    line += "，需避免交叉接触"
                lines.append(line)
            parts.append("；".join(lines))
        if pending:
            details = "；".join(
                f"「{dish}」过敏原未纳入标准规则，需人工确认后再定"
                + (f"（{reason}）" if reason else "")
                for dish, reason in pending
            )
            parts.append(details)
        if adjusted:
            details = "；".join(
                f"「{dish}」{reason or '按成员需求调整'}"
                for dish, reason in adjusted
            )
            parts.append(details)
        if not blocked and not pending and not adjusted:
            parts.append("可按共同做法正常食用")
        elif bucket["eatable"]:
            parts.append("其余菜品可按共同做法正常食用")
        adjustments.append(f"{member}：" + "；".join(parts))
    return adjustments
