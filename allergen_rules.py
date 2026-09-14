# allergen_rules.py：过敏原确定性硬护栏（L3）
# 依据：GB 7718-2011 4.4.3（现行，推荐标示）；
#       GB 7718-2025 八大类致敏物质（2027-03-16 起强制实施）。
#
# 过敏原判断不能交给 RAG 或 LLM：top-k 属于近似召回，漏掉一次就可能导致
# 严重安全事故。本模块只做可重复的确定性字符串匹配，RAG 仅可作为命中后的
# 出处补充，不参与“拦不拦”的判断。

from __future__ import annotations

import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List


ALLERGEN_SOURCE = (
    "GB 7718-2011 4.4.3；"
    "GB 7718-2025（2027-03-16 实施）八大类致敏物质"
)

ALLERGEN_RULES: Dict[str, Dict] = {
    "gluten": {
        "label": "含麸质的谷物及其制品",
        "aliases": [
            "小麦", "黑麦", "大麦", "燕麦", "斯佩耳特小麦", "面粉", "全麦",
            "麦麸", "面筋", "麦芽", "面包糠", "面条", "馒头", "饺子皮",
            "蛋糕", "饼干", "天妇罗粉", "小麦淀粉", "麸质", "gluten",
        ],
        "hidden": [
            "酱油", "生抽", "老抽", "蚝油", "豆瓣酱", "甜面酱", "素肉",
            "面筋", "啤酒", "大麦茶",
        ],
        "maybe_hidden": ["调味汁", "醋"],
        "exclusions": [],
    },
    "crustacean": {
        "label": "甲壳纲类动物及其制品",
        "aliases": [
            "虾", "龙虾", "蟹", "虾仁", "虾米", "虾皮", "虾酱", "蟹肉",
            "蟹黄", "小龙虾", "基围虾", "明虾", "对虾", "青虾", "白灼虾",
            "虾滑", "河虾", "海虾", "虾子", "虾籽", "虾脑", "螃蟹",
            "梭子蟹", "大闸蟹", "青蟹", "虾粉",
        ],
        "hidden": ["虾油"],
        "maybe_hidden": ["XO酱", "xo酱", "海鲜酱", "沙茶酱", "虾味"],
        "exclusions": ["蟹柳", "蟹肉棒", "蟹味棒", "蟹味菇", "虾青素"],
    },
    "fish": {
        "label": "鱼类及其制品",
        "aliases": [
            "鱼", "带鱼", "三文鱼", "鳕鱼", "金枪鱼", "鲈鱼", "鲽鱼",
            "鱼丸", "鱼豆腐", "鱼露", "鱼籽", "鲅鱼", "鲳鱼", "黄花鱼",
            "鲫鱼", "草鱼", "黑鱼", "龙利鱼", "巴沙鱼", "海鲜",
        ],
        "hidden": [
            "鱼露", "木鱼花", "柴鱼片", "凯撒沙拉酱", "辣酱油", "鱼粉",
            "海鲜酱",
        ],
        "maybe_hidden": ["鱼汤", "鱼味"],
        "exclusions": [
            "鱼香", "鱼香肉丝", "鱼腥草", "鱿鱼", "墨鱼", "章鱼", "鲍鱼",
            "海鲜菇",
        ],
    },
    "egg": {
        "label": "蛋类及其制品",
        "aliases": [
            "蛋", "鸡蛋", "鸭蛋", "咸鸭蛋", "蛋液", "蛋白", "蛋黄",
            "蛋粉", "蛋清", "蛋黄酱", "鹌鹑蛋", "皮蛋", "松花蛋",
            "咸蛋", "煎蛋", "蛋花", "蛋羹", "蛋皮", "蛋饺",
        ],
        "hidden": ["蛋黄酱", "沙拉酱", "蛋挞液", "蛋白霜", "美乃滋", "卡仕达酱"],
        "maybe_hidden": ["蛋糕", "饼干", "蛋卷"],
        "exclusions": ["蛋白质", "蛋白酶"],
    },
    "peanut": {
        "label": "花生及其制品",
        "aliases": ["花生", "花生酱", "花生碎", "花生粉", "花生油"],
        "hidden": ["沙嗲酱", "沙茶酱"],
        "maybe_hidden": ["混合坚果", "坚果碎"],
        "exclusions": [],
    },
    "soy": {
        "label": "大豆及其制品",
        "aliases": [
            "大豆", "黄豆", "豆浆", "豆腐", "豆干", "腐竹", "豆豉",
            "纳豆", "味噌", "毛豆", "黄豆芽", "大豆油", "大豆蛋白",
            "植物蛋白", "豆奶", "豆粉", "豆皮", "油豆腐", "千张",
        ],
        "hidden": [
            "酱油", "生抽", "老抽", "蚝油", "豆豉", "味噌", "素肉",
            "植物蛋白", "大豆卵磷脂", "黄豆酱", "豆瓣酱", "腐乳",
            "豆腐乳",
        ],
        "maybe_hidden": ["调味酱", "复合调味料"],
        "exclusions": [],
    },
    "milk": {
        "label": "乳及乳制品（包括乳糖）",
        "aliases": [
            "奶", "牛奶", "奶粉", "奶油", "黄油", "奶酪", "芝士", "酸奶",
            "炼乳", "乳清", "乳清蛋白", "酪蛋白", "乳糖", "酥油",
            "淡奶油", "鲜奶", "羊奶", "乳酪", "干酪", "起司", "乳制品",
        ],
        "hidden": [
            "奶油浓汤", "冰淇淋", "牛奶巧克力", "奶茶", "芝士粉",
            "人造奶油",
        ],
        "maybe_hidden": ["沙拉酱", "蛋糕", "饼干", "面包"],
        "exclusions": ["乳胶", "乳胶漆", "乳化", "乳化剂", "奶白菜"],
    },
    "nuts": {
        "label": "坚果及其果仁类制品",
        "aliases": [
            "坚果", "杏仁", "扁桃仁", "核桃", "胡桃", "腰果", "开心果",
            "碧根果", "美洲山核桃", "夏威夷果", "澳洲坚果", "巴西坚果",
            "榛子", "松子", "核桃油", "杏仁露",
        ],
        "hidden": ["青酱", "pesto", "坚果酱"],
        "maybe_hidden": ["巧克力"],
        "exclusions": ["核桃纹", "核桃木", "核桃壳"],
    },
}

# 非中国八大类，默认关闭；产品需要时可显式启用。
OPTIONAL_ALLERGENS: Dict[str, Dict] = {
    "sesame": {
        "label": "芝麻及其制品",
        "aliases": [
            "芝麻", "芝麻油", "芝麻酱", "芝麻糊", "芝麻粉", "麻酱", "麻油",
            "tahini",
        ],
        "hidden": [],
        "maybe_hidden": [],
        "exclusions": [],
    },
    "mollusc": {
        "label": "软体动物",
        "aliases": ["蛤", "牡蛎", "扇贝", "鱿鱼", "章鱼", "鲍鱼", "墨鱼"],
        "hidden": [],
        "maybe_hidden": [],
        "exclusions": [],
    },
    "sulphite": {
        "label": "二氧化硫和亚硫酸盐",
        "aliases": ["亚硫酸盐", "二氧化硫"],
        "hidden": [],
        "maybe_hidden": [],
        "exclusions": [],
    },
}


_NORMALIZATION_ALIASES = {
    "gluten": [
        "gluten", "麸质", "麸质过敏", "小麦过敏", "含麸质", "含麸质的谷物",
    ],
    "crustacean": [
        "甲壳纲", "甲壳类", "甲壳动物", "虾过敏", "蟹过敏", "海鲜过敏",
    ],
    "fish": ["鱼类", "鱼过敏", "海鲜", "海味", "河鲜"],
    "egg": ["蛋类", "鸡蛋过敏", "蛋过敏"],
    "peanut": ["花生过敏"],
    "soy": ["大豆过敏", "黄豆过敏", "豆制品", "大豆制品", "豆类制品"],
    "milk": [
        "乳制品", "奶制品", "乳类", "乳糖不耐", "乳糖不耐受", "牛奶过敏",
        "奶过敏", "乳过敏",
    ],
    "nuts": ["坚果过敏", "树坚果", "果仁"],
    "sesame": ["芝麻过敏"],
    "mollusc": ["软体动物", "贝类"],
    "sulphite": ["亚硫酸盐", "二氧化硫"],
}

# 一个自由文本可扩展到多个枚举值，例如“海鲜”同时按甲壳纲和鱼类保护。
_EXPANSIONS = {
    "海鲜": ["crustacean", "fish"],
    "海味": ["crustacean", "fish"],
    "河鲜": ["crustacean", "fish"],
    "海鲜过敏": ["crustacean", "fish"],
}

_UNRESOLVED_ALREADY_LOGGED = set()


def _clean(text: object) -> str:
    """统一空白、大小写和常见分隔符，避免输入格式影响归一结果。"""
    return re.sub(r"[\s,，、;；|/]+", "", str(text or "")).lower()


def _log_unresolved(text: object) -> None:
    """记录无法归一的过敏原，但日志失败绝不能阻断主流程。"""
    raw = str(text or "").strip()
    if not raw:
        return
    try:
        warnings.warn(
            f"无法归一过敏原「{raw}」，已回退到提示词约束。",
            UserWarning,
            stacklevel=3,
        )
    except Exception:
        pass
    if raw in _UNRESOLVED_ALREADY_LOGGED:
        return
    _UNRESOLVED_ALREADY_LOGGED.add(raw)
    try:
        path = Path(__file__).resolve().parent / "data" / "allergen_unresolved.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{datetime.now().isoformat(timespec='seconds')}\t{raw}\n"
            )
    except Exception:
        # 归一日志只是可观测性补强，磁盘异常不能影响安全审计本身。
        pass


def _all_rules(use_optional: bool = False) -> Dict[str, Dict]:
    rules = dict(ALLERGEN_RULES)
    if use_optional:
        rules.update(OPTIONAL_ALLERGENS)
    return rules


def normalize_allergens(text: object, log_unresolved: bool = True) -> List[str]:
    """自由文本过敏原 → 一个或多个八大类 code。

    建档字段保持自由文本，归一在读取和审计时进行，兼容存量档案。
    无法归一时记录告警与 data/allergen_unresolved.log，同时由调用方继续
    保留原提示词约束，不能静默失效。
    """
    compact = _clean(text)
    if not compact:
        return []

    found: List[str] = []
    for trigger, codes in _EXPANSIONS.items():
        if _clean(trigger) in compact:
            for code in codes:
                if code not in found:
                    found.append(code)

    for code in _all_rules(use_optional=True):
        if compact == code:
            return [code]

    candidates = [(code, alias) for code, aliases in _NORMALIZATION_ALIASES.items()
                  for alias in aliases]
    candidates.sort(key=lambda item: len(_clean(item[1])), reverse=True)
    for code, alias in candidates:
        if _clean(alias) in compact:
            if code not in found:
                found.append(code)

    for code, rule in _all_rules(use_optional=True).items():
        if code in found:
            continue
        aliases = list(rule.get("aliases") or [])
        aliases.append(rule.get("label", ""))
        for alias in sorted(aliases, key=lambda item: len(str(item)), reverse=True):
            if _clean(alias) and _clean(alias) in compact:
                if code not in found:
                    found.append(code)

    if found:
        return found

    if log_unresolved:
        _log_unresolved(text)
    return []


def normalize_allergen(text: str) -> str | None:
    """兼容单值调用：返回首个归一 code，无法归一返回 None。"""
    found = normalize_allergens(text)
    return found[0] if found else None


def allergen_label(allergen: object) -> str:
    """把 code 或自由文本转成稳定的中文展示名。"""
    codes = normalize_allergens(allergen, log_unresolved=False)
    if codes:
        return _all_rules(use_optional=True)[codes[0]].get("label", codes[0])
    return str(allergen or "").strip()


def _excluded_spans(text: str, exclusions: Iterable[str]) -> List[tuple]:
    spans = []
    for word in exclusions:
        needle = str(word or "")
        if needle:
            spans.extend((m.start(), m.end()) for m in re.finditer(re.escape(needle), text))
    return spans


def _in_spans(start: int, end: int, spans: List[tuple]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _first_match(
    text: str,
    keywords: Iterable[str],
    exclusion_spans: List[tuple],
) -> str:
    """返回最早出现的可用关键词；同一位置优先更长词，降低“虾”覆盖“虾仁”的风险。"""
    matches = []
    for word in keywords:
        needle = str(word or "")
        if not needle:
            continue
        for match in re.finditer(re.escape(needle), text):
            if _in_spans(match.start(), match.end(), exclusion_spans):
                continue
            matches.append((match.start(), -len(needle), needle))
    if not matches:
        return ""
    matches.sort()
    return matches[0][2]


# 未归一过敏原的统一话术：既不假装有确定性保护，也不硬猜词表造成误报。
UNRESOLVED_ALLERGEN_ADVICE = "未纳入标准规则，仅按原文提醒，需人工确认"


def resolve_allergens(
    allergens: Iterable[object],
    use_optional: bool = True,
) -> tuple[List[str], List[str]]:
    """把自由文本过敏原拆成（已归一 code 列表, 未归一原文列表）。

    未归一的原文**不丢弃**：它没有确定性规则覆盖，只靠原文与提示词兜底。
    调用方必须把它转成用户可见的提醒——否则等于「静默失效」，
    用户以为有硬护栏、实际什么都没查。
    """
    codes: List[str] = []
    unresolved: List[str] = []
    rules = _all_rules(use_optional=use_optional)
    for raw in allergens or []:
        text = str(raw or "").strip()
        if not text:
            continue
        found = [code for code in normalize_allergens(text) if code in rules]
        if not found:
            if text not in unresolved:
                unresolved.append(text)
            continue
        for code in found:
            if code not in codes:
                codes.append(code)
    return codes, unresolved


def _resolved_codes(allergens: Iterable[object], use_optional: bool) -> List[str]:
    return resolve_allergens(allergens, use_optional=use_optional)[0]


def unresolved_allergen_advisories(allergens: Iterable[object]) -> List[Dict]:
    """未归一过敏原 → 用户可见的 warn 级提醒。

    刻意不做同义词猜测：宁可少报并明说「需人工确认」，也不制造
    「看起来查过了」的虚假安全感。未归一原文仍写入 allergen_unresolved.log。
    """
    _, unresolved = resolve_allergens(allergens, use_optional=True)
    items: List[Dict] = []
    for text in unresolved:
        items.append({
            "condition": f"过敏原:{text}",
            "keyword": text,
            "message": f"「{text}」未纳入标准过敏原规则，仅按原文提醒",
            "source": ALLERGEN_SOURCE,
            "dimension": "allergen_unresolved",
            "advice": UNRESOLVED_ALLERGEN_ADVICE,
        })
    return items


def _audit_tier(
    text: object,
    allergens: Iterable[object],
    tier: str,
    use_optional: bool = False,
) -> List[Dict]:
    source_text = str(text or "")
    if not source_text.strip():
        return []

    violations: List[Dict] = []
    rules = _all_rules(use_optional=use_optional)
    for code in _resolved_codes(allergens, use_optional=use_optional):
        rule = rules[code]
        exclusions = _excluded_spans(source_text, rule.get("exclusions") or [])
        alias_hit = _first_match(source_text, rule.get("aliases") or [], exclusions)
        hidden_hit = _first_match(source_text, rule.get("hidden") or [], exclusions)
        maybe_hit = _first_match(source_text, rule.get("maybe_hidden") or [], exclusions)

        # 优先级固定为 exclusions > aliases > hidden > maybe_hidden。
        if alias_hit:
            hit, level = alias_hit, "hard"
        elif hidden_hit:
            hit, level = hidden_hit, "hard"
        elif maybe_hit:
            hit, level = maybe_hit, "notice"
        else:
            continue
        if level != tier:
            continue

        label = rule.get("label", code)
        message = f"必须完全不含{label}，包括调料与隐含来源"
        if code == "gluten":
            message += "；酱油等含麸质调料可改用椰子氨基酸或明确标注无麸质的酱油"
        violations.append({
            "condition": f"过敏原:{label}",
            "keyword": hit,
            "message": message,
            "source": ALLERGEN_SOURCE,
            "dimension": "allergen" if tier == "hard" else "allergen_notice",
        })
    return violations


def audit_allergens(
    text: str,
    allergens: List,
    use_optional: bool = False,
) -> List[Dict]:
    """确定性审计菜谱文本；只返回 aliases / hidden 的硬命中。

    返回结构与 nutrition_rules.audit 对齐，并额外带 dimension 字段。
    maybe_hidden 不进入硬拦截，避免把“可能含”当成“确定含”造成大面积误拦。
    具体分派由 constraint_rules 统一负责，本函数保留原公开签名与返回结构。
    """
    try:
        from constraint_rules import audit_allergens_dimension

        return audit_allergens_dimension(
            text,
            allergens,
            use_optional=use_optional,
        )
    except ImportError:
        # 单文件运行/测试时允许脱离统一引擎，行为与旧接口完全一致。
        return _audit_tier(text, allergens, tier="hard", use_optional=use_optional)


def audit_allergen_advisories(
    text: str,
    allergens: List,
    use_optional: bool = False,
) -> List[Dict]:
    """返回 maybe_hidden 的提醒项；调用方可展示提示，但不得用它做硬拦截。"""
    return _audit_tier(text, allergens, tier="notice", use_optional=use_optional)


def describe_allergens(violations: List[Dict]) -> str:
    """把过敏原命中格式化成与 nutrition_rules.describe 同风格的中文文本。"""
    if not violations:
        return "无过敏原命中"
    lines = []
    for violation in violations:
        lines.append(
            f"- {violation['condition']}：命中「{violation['keyword']}」"
            f"→ {violation['message']}（来源：{violation['source']}）"
        )
    return "\n".join(lines)


# 确定性替代菜候选池：每项同时包含菜名、主料和调料，避免只查菜名漏掉隐含来源。
_SAFE_DISH_CANDIDATES = [
    ("清蒸鸡腿", "鸡腿 葱 姜 盐"),
    ("蒜蓉炒青菜", "青菜 蒜 盐"),
    ("番茄鸡蛋汤", "番茄 鸡蛋 盐"),
    ("白灼菜心", "菜心 葱 姜"),
    ("清蒸鲈鱼", "鲈鱼 葱 姜 盐"),
    ("土豆炖牛肉", "土豆 牛肉 葱 姜 盐"),
    ("冬瓜排骨汤", "冬瓜 排骨 姜 盐"),
    ("香菇滑鸡", "香菇 鸡肉 姜 盐"),
    ("清炒西兰花", "西兰花 蒜 盐"),
    ("蒜蓉粉丝蒸娃娃菜", "娃娃菜 粉丝 蒜 盐"),
    ("玉米胡萝卜炖排骨", "玉米 胡萝卜 排骨 姜 盐"),
    ("芹菜炒牛肉", "芹菜 牛肉 姜 盐"),
    ("青椒炒肉丝", "青椒 猪肉 姜 盐"),
    ("醋溜土豆丝", "土豆 米醋 青椒"),
    ("番茄炖牛腩", "番茄 牛腩 姜 盐"),
    ("香煎鸡胸肉", "鸡胸肉 黑胡椒 盐"),
    ("清炒油麦菜", "油麦菜 蒜 盐"),
    ("蒸南瓜", "南瓜"),
    ("白灼西兰花", "西兰花 姜"),
    ("盐水鸭", "鸭肉 姜 花椒 盐"),
    ("清炖羊肉萝卜", "羊肉 白萝卜 姜 盐"),
    ("蒜香菠菜", "菠菜 蒜 盐"),
    ("香菇青菜", "香菇 青菜 盐"),
    ("香煎三文鱼", "三文鱼 黑胡椒 盐"),
    ("芦笋炒牛肉", "芦笋 牛肉 姜 盐"),
    ("木耳炒山药", "木耳 山药 蒜 盐"),
    ("丝瓜炒鸡蛋", "丝瓜 鸡蛋 盐"),
    ("白菜豆腐汤", "白菜 豆腐 姜 盐"),
    ("土豆胡萝卜炖鸡", "土豆 胡萝卜 鸡肉 姜 盐"),
    ("清炒莴笋", "莴笋 蒜 盐"),
]


def suggest_safe_dishes(
    allergens: List,
    k: int = 3,
    use_optional: bool = False,
) -> List[str]:
    """从内置候选池返回前 k 道不命中硬过敏原的菜；确定性、不调用 LLM。"""
    safe = []
    for name, ingredients in _SAFE_DISH_CANDIDATES:
        if audit_allergens(
            f"{name} {ingredients}",
            allergens,
            use_optional=use_optional,
        ):
            continue
        safe.append(name)
        if len(safe) >= max(0, int(k)):
            break
    if safe:
        return safe
    labels = "、".join(
        dict.fromkeys(allergen_label(item) for item in (allergens or []))
    )
    return [f"告诉我你家现有的食材，我按不含{labels}重新配"]


if __name__ == "__main__":
    samples = [
        ("鱼香肉丝", ["fish"]),
        ("优质蛋白质", ["egg"]),
        ("生抽炒饭", ["soy", "gluten"]),
        ("XO酱炒饭", ["crustacean"]),
    ]
    for sample_text, sample_allergens in samples:
        print(sample_text, audit_allergens(sample_text, sample_allergens))
