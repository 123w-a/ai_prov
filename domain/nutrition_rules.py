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
# 这些本身也是高钠食物，判定口径与盐/酱油一致（走「限量语境」判定）。
# 它只决定「怎么判」，不改变各病种 forbidden 词表里到底有哪些词。
_SALT_QUALIFIABLE = set(_SALT_SEASONING) | {"榨菜", "泡菜", "咸菜", "酱菜"}
_PROCESSED_MEAT = ["腊肉", "香肠", "腊肠", "培根", "咸肉", "火腿", "加工红肉"]
_ALCOHOL_TERMS = [
    "饮酒", "喝酒", "酒精", "白酒", "啤酒", "红酒", "葡萄酒",
    "黄酒", "米酒", "烈酒", "酒酿",
]

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
        # 不用裸「酒」匹配，避免把作为烹饪配料的「料酒」误判为饮酒。
        "forbidden": ["油炸食品", "含糖烘焙", "糖果", "肥肉", "高糖水果", "高淀粉蔬菜"] + _ALCOHOL_TERMS,
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
    ("高血压", ["高血压", "血压高", "血压偏高", "血压有点高", "血压不稳"]),
    ("糖尿病", ["糖尿病", "血糖"]),
    ("高脂血症", ["高血脂", "高脂血症", "血脂"]),
    ("痛风", ["痛风", "高尿酸", "尿酸"]),
    ("慢性肾脏病", ["肾病", "肾脏", "慢性肾脏", "肾功"]),
    ("肥胖", ["肥胖", "减肥", "减重", "控制体重"]),
    ("孕期", ["孕期", "孕妇", "妊娠", "怀孕"]),
]


_CONDITION_NEGATION_RE = re.compile(
    r"(?:没有|没|不是|并非|未)(?:(?:被|明确|确诊|诊断|患有|得过|任何|为)){0,5}$"
)
# 关键词**之前**出现这些词，说明这一句在讲「限量」，不是在下料。
# 不能只看紧邻前缀：实测合规菜谱里的「不额外加盐」（中间隔着「额外」）、
# 「不加鸡精味精」（味精前面是「鸡精」）都会被漏判成真实下料。
_SALT_QUALIFIERS = (
    "不加", "不放", "不用", "不要", "不超", "少放", "少用", "少吃",
    "少", "低", "减", "无", "避免", "远离", "忌", "限制", "控制", "限量",
)
# 关键词**之后**出现这些词，同样说明这一句在讲「限量/警示」：
# 「盐可以几乎不放」「生抽减半」「钠极高」。
_SALT_TRAIL_QUALIFIERS = (
    "不放", "不加", "不用", "不要多", "别多放", "少放", "少用", "少加",
    "少吃", "少些", "少许", "少",
    "控制", "限制", "限量", "不超", "不超过", "低于", "以下", "降至", "≤",
    "减半", "减量", "减少", "减",
    "钠极高", "钠很高", "钠超标", "钠高", "高钠", "含钠高",
)
# 同句内更远的限量词（不紧邻也算）：覆盖指南引用
# 「减少食盐及含钠调味品（酱油、酱类、蚝油、鸡精、味精等）」——
# 限定词与关键词之间隔着一整个并列枚举，按紧邻前缀看必然漏判。
_SALT_CLAUSE_QUALIFIERS = (
    "限量", "控制", "限制", "避免", "远离", "减少", "减", "少", "低", "无", "忌", "免",
)
# 否定 + 短间隔 + 下料动作：覆盖「不额外加盐」「不要用盐腌黄瓜出水」。
# 中间允许 0-6 个字符（如「额外」），但不能跨标点，否则会把上一句的否定读进来。
_SALT_NEGATION_WORDS = "不没无别勿免杜绝避免远离忌拒绝禁止"
_SALT_NEGATION_DOSE_RE = re.compile(
    rf"[{_SALT_NEGATION_WORDS}][^，。；！？\n、]{{0,6}}[加放用下撒淋兑要吃碰选多]"
)
# 限量词与关键词之间若已出现这些真下料动作，说明限量词管的是另一样东西：
# 「少油放盐 10 克」里的「少」不能给「盐」开脱。
_SALT_GAP_DOSE_VERBS = ("加", "放", "下", "撒", "淋", "兑")
# 限定词与关键词之间出现这些词，是「翻案」而不是「限量」：
# 「本来想不加盐但实际放盐 10 克」必须继续判为违规。
_SALT_REVERSAL_MARKERS = ("但", "不过", "却", "实际", "其实", "改用", "换成", "只是")
# 限定词只在同一个「限量语境」内有效，跨句读不算：否则
# 「少油的一道菜，盐 5g」会被前一句的「少」误判成合规表述。
_SALT_CLAUSE_DELIMITERS = "。！？；\n\r，,：:"
_SALT_LOOKBACK_CHARS = 40
_SALT_LOOKAHEAD_CHARS = 12
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


def _clause_before(compact: str, start: int) -> str:
    """取关键词之前同一句内的文本（回看有上限，遇标点截断）。

    限定词只在同一个「限量语境」内有效：否则「少油的一道菜，盐 5g」
    会被上一句的「少」误判成合规表述。
    """
    window = compact[max(0, start - _SALT_LOOKBACK_CHARS):start]
    cut = max(window.rfind(ch) for ch in _SALT_CLAUSE_DELIMITERS)
    return window[cut + 1:] if cut >= 0 else window


def _reversed_by(text: str) -> bool:
    """文本里出现翻案词（但/实际/改用…），说明前面的「限量」已被推翻。"""
    return any(marker in text for marker in _SALT_REVERSAL_MARKERS)


def _ends_with_reversed(text: str) -> bool:
    """限定词前面紧跟否定，是「不少盐」「不能不加盐」这类翻案，不算限量。"""
    return any(text.endswith(item) for item in _REVERSED_QUALIFIERS)


def _salt_is_limited(compact: str, start: int, end: int) -> bool:
    """判断这一处盐/高钠调味料是在讲「少放/别放/减量」，还是真在下料。

    四种证据，任一成立即视为限量表述（不算违规）：
      1. 紧邻前缀限定词——保留原口径，覆盖「少盐」「不加盐」；
      2. 同句内的否定 + 下料动作——覆盖「不额外加盐」「不要用盐腌黄瓜出水」；
      3. 同句内更远的限量词、且中间没有真下料动作——
         覆盖指南引用「减少食盐及含钠调味品（酱油、蚝油…）」；
      4. 关键词后紧跟的限量/警示词——覆盖「盐可以几乎不放」「生抽减半」「钠极高」。
    """
    clause = _clause_before(compact, start)
    cursor = len(clause)  # 关键词在 clause 内的位置（clause 恰好结束于关键词）
    if _qualified_by_suffix(clause, _SALT_QUALIFIERS):
        return True
    for match in _SALT_NEGATION_DOSE_RE.finditer(clause, 0, cursor):
        middle = match.group(0)[1:-1]
        if any(ch in _SALT_NEGATION_WORDS for ch in middle):
            continue  # 「不能不加」：否定词内部还套着否定，是翻案不是限量
        if _ends_with_reversed(clause[:match.start()]):
            continue
        if _reversed_by(clause[match.end():cursor]):
            continue
        return True
    for qualifier in sorted(_SALT_CLAUSE_QUALIFIERS, key=len, reverse=True):
        index = clause.rfind(qualifier, 0, cursor)
        if index < 0:
            continue
        if _ends_with_reversed(clause[:index]):
            continue
        gap = clause[index + len(qualifier):cursor]
        if _reversed_by(gap) or any(verb in gap for verb in _SALT_GAP_DOSE_VERBS):
            continue
        return True
    tail = compact[end:end + _SALT_LOOKAHEAD_CHARS]
    for qualifier in sorted(_SALT_TRAIL_QUALIFIERS, key=len, reverse=True):
        index = tail.find(qualifier)
        if index < 0:
            continue
        if _ends_with_reversed(tail[:index]):
            continue
        return True
    return False


def _has_unqualified_occurrence(text: str, keyword: str, mode: str) -> bool:
    """Return True when at least one keyword occurrence expresses actual use/state."""
    compact = re.sub(r"\s+", "", text or "")
    for match in re.finditer(re.escape(keyword), compact):
        before = compact[max(0, match.start() - 12):match.start()]
        if mode == "condition" and _CONDITION_NEGATION_RE.search(before):
            continue
        if mode == "salt" and _salt_is_limited(compact, match.start(), match.end()):
            continue
        if mode == "forbidden" and _qualified_by_suffix(before, _FORBIDDEN_QUALIFIERS):
            continue
        return True
    return False


# 档案 conditions 的匹配词表：比消息侧更宽（「孕18周」这类自由文本也要命中孕期），
# 备孕误入孕期护栏方向保守可接受（禁酒禁生食对备孕同样无害）。
_PROFILE_CONDITION_KEYWORDS = [
    (rule, kws + ["孕"] if rule == "孕期" else kws)
    for rule, kws in _CONDITION_KEYWORDS
]


def conditions_from_profile(profile: dict) -> List[str]:
    """从结构化档案（v2 成员画像）的 conditions 自由文本映射到 RULES 键。

    档案里写了「痛风」「孕18周」时，即使消息里不提，硬护栏也要同口径启用——
    这是 P1 多画像与 L3 硬护栏的接线点。
    """
    found: List[str] = []
    for cond in (profile or {}).get("conditions") or []:
        text = str(cond)
        for rule, kws in _PROFILE_CONDITION_KEYWORDS:
            if rule not in found and any(kw in text for kw in kws):
                found.append(rule)
    return found


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
            mode = "salt" if kw in _SALT_QUALIFIABLE else "forbidden"
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
