# nutrition_rules.py：确定性硬护栏规则引擎（L3 硬护栏）
# ---------------------------------------------------------------------------
# 设计目标：LLM 可能幻觉或漏掉忌口，RAG 召回也不保证 100% 全覆盖，
# 因此用一套"不依赖模型的确定性规则"在输出前做最后一道硬审计。
#
# 规则来源：全部抽取自 D:\私厨资料 中的国家食养指南 PDF，每条带 source 出处
#          （文件名 + 页码）。赛前可逐条核对、替换为正式发布版数值。
#          注：孕期规则权威源已补——见 4_特殊人群膳食指南/ 下两份中国营养学会《2022》指南解读课件。
#
# 对外接口：
#   detect_conditions(text) -> list[str]      从用户原话推断适用人群/病种
#   audit(text, conditions) -> list[dict]     审计一段菜谱文本，返回违禁命中
# ---------------------------------------------------------------------------
from typing import List, Dict
import re

# 共享的高危食材词表（多个病种共用，避免重复）
_ORGAN_MEAT = ["动物内脏", "猪肝", "鸡肝", "鸭肝", "猪肾", "鸡肾", "鸭肠", "脑", "腰子"]
_HIGH_PURINE_SEAFOOD = ["沙丁鱼", "凤尾鱼", "带鱼", "秋刀鱼", "牡蛎", "蛤蜊", "虾", "蟹", "贝", "鱼干"]
_SALT_SEASONING = ["盐", "酱油", "生抽", "老抽", "蚝油", "鸡精", "味精", "豆瓣酱", "辣椒酱", "豆腐乳", "腐乳"]
_PROCESSED_MEAT = ["腊肉", "香肠", "腊肠", "培根", "咸肉", "火腿", "加工红肉"]

# 钠敏感病种：启用食盐上限检查
SODIUM_SENSITIVE = {"高血压", "糖尿病", "高脂血症", "慢性肾脏病", "肥胖"}

# 每个病种一条规则。forbidden 为"在菜谱文本中出现即判违禁"的关键词；
# message 为给模型/用户的改写建议；source 为出处，便于竞赛可溯源演示。
RULES: Dict[str, Dict] = {
    "高血压": {
        "forbidden": _PROCESSED_MEAT + _SALT_SEASONING + ["动物内脏", "油炸", "咸菜", "榨菜", "泡菜", "酱菜", "酒"],
        "salt_cap_g": 5,
        "message": "限制钠盐摄入，每日食盐逐步降至 5g 以下；少吃加工红肉制品与高盐调味品，限制脂肪胆固醇",
        "source": "成人高血压食养指南（2023年版）p6-7",
    },
    "糖尿病": {
        "forbidden": ["肥肉", "烟熏", "烘烤", "腌制"] + _PROCESSED_MEAT
                    + ["白砂糖", "冰糖", "麦芽糖", "蜂蜜", "含糖饮料", "甜饮料", "果糖", "酒"],
        "salt_cap_g": 5,
        "message": "主食定量、少油少盐限糖；少吃肥肉与加工肉制品，食盐每日不宜超过 5g",
        "source": "成人糖尿病食养指南（2023年版）p7,p11",
    },
    "高脂血症": {
        "forbidden": ["动物脑", "动物内脏", "油炸", "油煎", "反式脂肪", "肥肉", "猪油", "黄油", "奶油", "酒"],
        "salt_cap_g": 5,
        "message": "限制总脂肪/饱和脂肪/胆固醇/反式脂肪酸（反式脂肪<2g/日）；少盐控糖，食盐≤5g",
        "source": "成人高脂血症食养指南（2023年版）p7-9",
    },
    "痛风": {
        "forbidden": _ORGAN_MEAT + ["浓汤", "老火汤", "肉汤", "高汤"] + _HIGH_PURINE_SEAFOOD
                    + ["啤酒", "酒", "果糖", "生冷"],
        "message": "限制高嘌呤食物（动物内脏/浓肉汤/部分海鲜）、果糖与饮酒；科学烹饪、少食生冷",
        "source": "成人高尿酸血症与痛风食养指南（2024年版）p7-8",
    },
    "慢性肾脏病": {
        "forbidden": ["浓汤", "老火汤", "烟熏", "烧烤", "腌制"] + _PROCESSED_MEAT
                    + _SALT_SEASONING + ["动物内脏", "酒"],
        "salt_cap_g": 5,
        "message": "少盐控油、限磷控钾；限制或禁食浓肉汤，少吃烟熏烧烤腌制与高盐调味品",
        "source": "成人慢性肾脏病食养指南（2024年版）p7,p11",
    },
    "肥胖": {
        "forbidden": ["油炸食品", "含糖烘焙", "糖果", "肥肉", "高糖水果", "高淀粉蔬菜", "酒"],
        "salt_cap_g": 5,
        "sugar_cap_g": 25,
        "message": "少吃高能量食物（油炸/含糖糕点/肥肉）；添加糖≤25g/日，食盐≤5g/日",
        "source": "成人肥胖食养指南（2024年版）p9",
    },
    # 权威源已补：4_特殊人群膳食指南/ 下两份中国营养学会《2022》指南解读课件（杨年红/杨振宇）
    "孕期": {
        "forbidden": ["酒", "生冷", "生食", "生鱼", "生蛋", "未熟", "高汞鱼", "汞", "烟熏", "浓茶", "咖啡"],
        "message": "禁酒、禁生冷生食（防李斯特菌/弓形虫）；避免高汞鱼类（金枪鱼/鲨鱼）；"
                    "补铁、选用碘盐、合理补叶酸与维生素D；孕吐严重少量多餐保碳水；"
                    "孕中晚期适量增奶鱼禽蛋瘦肉；限浓茶咖啡",
        "source": "中国备孕和孕期妇女膳食指南（2022）解读（杨年红）p4；"
                  "中国哺乳期妇女膳食指南（2022）解读（杨振宇）p5",
    },
}

# 用户原话 -> 病种 的关键词映射（用于自动识别适用规则）
_CONDITION_KEYWORDS = [
    ("高血压", ["高血压", "血压高"]),
    ("糖尿病", ["糖尿病", "血糖"]),
    ("高脂血症", ["高血脂", "高脂血症", "血脂"]),
    ("痛风", ["痛风", "高尿酸", "尿酸"]),
    ("慢性肾脏病", ["肾病", "肾脏", "慢性肾脏", "肾功"]),
    ("肥胖", ["肥胖", "减肥", "减重"]),
    ("孕期", ["孕期", "孕妇", "妊娠", "怀孕"]),
]


_CONDITION_NEGATION_RE = re.compile(
    r"(?:没有|没|不是|并非|未)(?:(?:被|明确|确诊|诊断|患有|得过|任何|为)){0,5}$"
)
_SALT_QUALIFIERS = ("不加", "不放", "少放", "少", "低", "减", "无")
_FORBIDDEN_QUALIFIERS = ("不吃", "不喝", "不放", "不加", "不含", "不要", "避免", "禁用", "拒绝")
_REVERSED_QUALIFIERS = (
    "不", "不能", "不要", "不做", "不采用", "不接受", "拒绝",
    "没有", "没", "不是", "并非", "未",
)


def _qualified_by_suffix(before: str, qualifiers) -> bool:
    for qualifier in sorted(qualifiers, key=len, reverse=True):
        if not before.endswith(qualifier):
            continue
        preceding = before[:-len(qualifier)]
        if any(preceding.endswith(item) for item in _REVERSED_QUALIFIERS):
            return False
        return True
    return False


def _has_unqualified_occurrence(text: str, keyword: str, mode: str) -> bool:
    """Return True when at least one keyword occurrence expresses actual use/state."""
    compact = re.sub(r"\s+", "", text or "")
    for match in re.finditer(re.escape(keyword), compact):
        before = compact[max(0, match.start() - 12):match.start()]
        if mode == "condition" and _CONDITION_NEGATION_RE.search(before):
            continue
        if mode == "salt" and _qualified_by_suffix(before, _SALT_QUALIFIERS):
            continue
        if mode == "forbidden" and _qualified_by_suffix(before, _FORBIDDEN_QUALIFIERS):
            continue
        return True
    return False


def detect_conditions(text: str) -> List[str]:
    """从用户原话推断需要启用哪些硬护栏规则。"""
    found = []
    for cond, kws in _CONDITION_KEYWORDS:
        if any(_has_unqualified_occurrence(text, kw, "condition") for kw in kws):
            found.append(cond)
    return found


def _total_g(text: str, keywords, units: str = r"g|克|ml|毫升") -> float:
    """尽力估算关键词后标注数量的克数总和（最佳努力，非精确）。

    长词优先并按文本区间去重："白砂糖30g"里的"糖"不再重复计入。
    """
    matches = []
    for kw in sorted(set(keywords), key=len, reverse=True):
        pattern = rf"{re.escape(kw)}\s*(?:约|大约)?\s*(\d+(?:\.\d+)?)\s*(?:{units})"
        for m in re.finditer(pattern, text):
            matches.append((m.start(), m.end(), float(m.group(1))))
    total, taken = 0.0, []
    for start, end, value in sorted(matches):
        if any(start < prev_end and end > prev_start for prev_start, prev_end in taken):
            continue
        taken.append((start, end))
        total += value
    return total


def _salt_total_g(text: str) -> float:
    """尽力从菜谱文本里估算食盐/高钠调料的克数（最佳努力，非精确）。"""
    return _total_g(text, ["盐", "酱油", "生抽", "老抽", "蚝油"])


def audit(text: str, conditions: List[str]) -> List[Dict]:
    """审计一段菜谱/回答文本，返回所有硬禁忌命中。

    返回元素：{"condition","keyword","message","source","todo_source"?}
    """
    violations: List[Dict] = []
    seen = set()
    for cond in conditions:
        rule = RULES.get(cond)
        if not rule:
            continue
        for kw in rule.get("forbidden", []):
            mode = "salt" if kw in _SALT_SEASONING else "forbidden"
            if _has_unqualified_occurrence(text, kw, mode) and (cond, kw) not in seen:
                seen.add((cond, kw))
                violations.append({
                    "condition": cond,
                    "keyword": kw,
                    "message": rule["message"],
                    "source": rule["source"],
                    **({"todo_source": True} if rule.get("todo_source") else {}),
                })
        # 钠上限检查
        if cond in SODIUM_SENSITIVE:
            cap = rule.get("salt_cap_g")
            if cap:
                total = _salt_total_g(text)
                if total > cap:
                    key = (cond, "高钠调料")
                    if key not in seen:
                        seen.add(key)
                        violations.append({
                            "condition": cond,
                            "keyword": f"食盐约{total:.0f}g(> {cap}g)",
                            "message": rule["message"],
                            "source": rule["source"],
                        })
        # 添加糖上限检查（如肥胖：添加糖≤25g/日）
        sugar_cap = rule.get("sugar_cap_g")
        if sugar_cap:
            total = _total_g(text, ["白砂糖", "冰糖", "麦芽糖", "糖"], r"g|克")
            if total > sugar_cap:
                key = (cond, "添加糖")
                if key not in seen:
                    seen.add(key)
                    violations.append({
                        "condition": cond,
                        "keyword": f"糖约{total:.0f}g(> {sugar_cap}g)",
                        "message": rule["message"],
                        "source": rule["source"],
                    })
    return violations


def describe(violations: List[Dict]) -> str:
    """把违禁命中格式化成给人/模型看的中文说明。"""
    if not violations:
        return "无硬禁忌命中"
    lines = []
    for v in violations:
        flag = " [待补权威源]" if v.get("todo_source") else ""
        lines.append(f"- {v['condition']}：命中「{v['keyword']}」→ {v['message']}（来源：{v['source']}{flag}）")
    return "\n".join(lines)


if __name__ == "__main__":
    # 自检：故意塞一个痛风违禁菜单，应当被拦下
    bad = "推荐一道老火汤炖猪肝，配啤酒，饭后吃点果糖点心"
    print("conditions:", detect_conditions("我痛风又尿酸高，想吃啥"))
    vs = audit(bad, ["痛风"])
    print(describe(vs))
