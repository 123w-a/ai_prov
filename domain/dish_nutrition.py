# dish_nutrition.py：按菜谱食材用量算「这顿」的营养数值（确定性查表，不臆造）
"""把「食材名 + 克数」累加成结果页那张六列营养表。

═══ 为什么在 domain 层、为什么要独立于 agent_tools ═══════════════════════════

`agent_tools/legacy.py` 里已经有一个 `calorie_lookup`（单食材查询），但它服务于
**Agent 的问答**（返回一段 JSON 文本给模型读）。这里要的是**后处理**：拿到结构化的
ingredients 后，用同一张国标表算出确定性数值，直接写进 answer。

依赖方向：`domain/` 是最底层，`agent/` 与 `agent_tools/` 都依赖它。所以本模块不能
import `agent_tools`，只能自己加载那张 CSV——这不理想（两处加载同一张表），但比让
domain 反向依赖工具层干净。若将来要合一，应该是把加载下沉到 domain、由 legacy 复用。

═══ 三条硬纪律（2026-10-04 与 lave 商讨后定） ════════════════════════════════

  1 **数值只能来自国标表。** 本模块不做任何估算、不外推、不按"标准份量"补齐克数。
     查不到就是查不到——`status=unavailable`。
  2 **缺失不许填 0。** 0 和"未收录"在营养表里是两件事：0 g 膳食纤维是一句结论，
     "未收录"只是一句实话。页面上必须能分辨。
  3 **不完整就不能叫"这顿的总量"。** 只要有一个食材没收录、或有一个食材没有克数，
     总量就只是**已覆盖食材的小计**——此时 status=partial，页面必须这么写。
     全齐才允许 complete。

六列的顺序、单位、以及国标表里的字段名由 NUTRIENT_FIELDS 一处定义，前端按同一顺序渲染。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# (返回给前端的键, 中文名, 单位, 国标表字段名)。顺序即页面上的行序。
NUTRIENT_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("energy_kcal", "能量", "kcal", "kcal"),
    ("protein_g", "蛋白质", "g", "protein"),
    ("fat_g", "脂肪", "g", "fat"),
    ("carb_g", "碳水化合物", "g", "carb"),
    ("fiber_g", "膳食纤维", "g", "fiber"),
    ("sodium_mg", "钠", "mg", "sodium"),
)

# 口语名 → 国标表里的那一条（表里是「主名（学名/别名）」形态）。
# 只写**确定的同物异名**；同一条目有多个形态（豆腐南/北、鸡蛋红皮/白皮）时，
# 取最常用的那个并在注释里写明，因为它必须选一个——不选就只能报"未收录"，
# 那对用户更糟。选中的 lookup_name 会随 facts 一起返回，页面上可追溯。
_FOOD_ALIAS: dict[str, str] = {
    # 主食
    "米饭": "大米（粳米）", "白米饭": "大米（粳米）", "白米": "大米（粳米）",
    "米": "大米（粳米）", "大米": "大米（粳米）", "珍珠米": "大米（粳米）",
    "面条": "面条（挂面）", "挂面": "面条（挂面）", "面": "面条（挂面）",
    "馒头": "馒头（蒸）", "面粉": "小麦粉（标准粉）", "小麦粉": "小麦粉（标准粉）",
    "燕麦": "燕麦片", "玉米": "玉米（鲜）", "地瓜": "红薯（甘薯）",
    "红薯": "红薯（甘薯）", "土豆": "土豆（马铃薯）", "马铃薯": "土豆（马铃薯）",
    "糯米": "糯米（江米）", "江米": "糯米（江米）",
    # 蔬菜
    "西兰花": "西兰花（绿菜花）", "花椰菜": "菜花（花椰菜）", "菜花": "菜花（花椰菜）",
    "卷心菜": "圆白菜（卷心菜）", "包菜": "圆白菜（卷心菜）", "圆白菜": "圆白菜（卷心菜）",
    "西红柿": "番茄（西红柿）", "番茄": "番茄（西红柿）",
    "青椒": "辣椒（青）", "彩椒": "甜椒（灯笼椒）", "灯笼椒": "甜椒（灯笼椒）",
    "藕": "藕（莲藕）", "莲藕": "藕（莲藕）", "马蹄": "荸荠（马蹄）", "荸荠": "荸荠（马蹄）",
    "蒜": "大蒜（蒜头）", "大蒜": "大蒜（蒜头）", "蒜头": "大蒜（蒜头）",
    "姜": "生姜", "葱": "大葱", "香葱": "小葱",
    "香菇": "香菇（鲜）", "平菇": "平菇（鲜）", "木耳": "木耳（水发）", "银耳": "银耳（水发）",
    "海带": "海带（水发）", "紫菜": "紫菜（干）",
    "空心菜": "空心菜（蕹菜）", "香菜": "香菜（芫荽）", "芫荽": "香菜（芫荽）",
    "芹菜": "芹菜（茎）", "白菜": "大白菜", "心里美": "心里美萝卜",
    # 豆制品与豆类
    # 豆腐只写「北」：南北豆腐热量差 <15%，而"豆腐"这个词必须落到一条上。
    "豆腐": "豆腐（北）", "老豆腐": "豆腐（北）", "嫩豆腐": "豆腐（南）",
    "豆干": "豆腐干", "香干": "豆腐干", "豆皮": "豆腐皮（油皮）",
    "大豆": "黄豆（大豆）", "黄豆": "黄豆（大豆）", "赤小豆": "红豆（赤小豆）",
    "红豆": "红豆（赤小豆）",
    # 蛋奶肉
    # 鸡蛋取「红皮」：与白皮差 <3%，红皮更常见。
    "鸡蛋": "鸡蛋（红皮）", "蛋": "鸡蛋（红皮）", "鸡子": "鸡蛋（红皮）",
    "牛奶": "牛奶（纯）", "纯牛奶": "牛奶（纯）", "奶粉": "奶粉（全脂）",
    "奶酪": "奶酪（干酪）", "芝士": "奶酪（干酪）",
    "猪肉": "猪肉（肥瘦）", "里脊": "猪里脊", "猪里脊肉": "猪里脊",
    "排骨": "猪排骨", "猪排": "猪排骨", "牛肉": "牛肉（肥瘦）", "牛腱": "牛腱子",
    "鸡肉": "鸡肉（整鸡）", "鸡胸": "鸡胸肉", "鸡胸脯": "鸡胸肉",
    "羊肉": "羊肉（肥瘦）", "羊排": "羊排",
    # 水产
    "虾": "对虾", "大虾": "对虾", "基围虾": "基围虾", "鲑鱼": "三文鱼（鲑鱼）",
    "三文鱼": "三文鱼（鲑鱼）", "生蚝": "牡蛎（海蛎子）", "牡蛎": "牡蛎（海蛎子）",
    "海蛎子": "牡蛎（海蛎子）", "蛤蜊": "花蛤（蛤蜊）", "花蛤": "花蛤（蛤蜊）",
    "鱿鱼": "鱿鱼（鲜）", "乌贼": "墨鱼（乌贼）", "墨鱼": "墨鱼（乌贼）",
    # 油与调料
    # 「油/食用油」取大豆油：家常最常用，且各植物油热量几乎相同（899 kcal/100g），
    # 选哪个对能量那一行影响 <1%。若菜谱写明花生油/橄榄油等，照写即可命中精确名。
    "油": "大豆油", "食用油": "大豆油", "植物油": "大豆油", "香油": "芝麻油（香油）",
    "芝麻油": "芝麻油（香油）", "盐": "食盐", "糖": "白糖（白砂糖）",
    "白糖": "白糖（白砂糖）", "白砂糖": "白糖（白砂糖）", "砂糖": "白糖（白砂糖）",
    "生抽": "生抽", "老抽": "老抽", "淀粉": "淀粉（玉米）", "生粉": "淀粉（玉米）",
}

_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _load_table() -> list[dict[str, Any]]:
    """加载国标《中国食物成分表》。文件缺失/读失败返回空表——此时一切查询都判未收录，
    绝不回退到任何内置近似值。"""
    rows: list[dict[str, Any]] = []
    path = _PROJECT_ROOT / "data" / "china_food_components.csv"
    try:
        if not path.exists():
            return rows
        with path.open(encoding="utf-8-sig", newline="") as f:
            for raw in csv.DictReader(f):
                name = (raw.get("食物名称") or "").strip()
                if not name:
                    continue

                def _num(v: Any) -> Optional[float]:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return None

                rows.append({
                    "name": name,
                    "kcal": _num(raw.get("能量kcal")),
                    "protein": _num(raw.get("蛋白质g")),
                    "fat": _num(raw.get("脂肪g")),
                    "carb": _num(raw.get("碳水化合物g")),
                    "fiber": _num(raw.get("膳食纤维g")),
                    "sodium": _num(raw.get("钠mg")),
                })
    except Exception:
        return []
    return rows


_FOOD_TABLE = _load_table()
_BY_NAME = {row["name"]: row for row in _FOOD_TABLE}
# 主名（去掉「（…）」）索引。仅在**恰好一个**条目用这个主名时才可用于回退匹配——
# 多个条目共用主名（如「豆腐（南）/豆腐（北）」）时不许猜，一律判未收录。
_BY_STEM: dict[str, Optional[dict[str, Any]]] = {}
for _row in _FOOD_TABLE:
    _stem = _row["name"].split("（")[0].strip()
    _BY_STEM[_stem] = None if _stem in _BY_STEM else _row


def lookup_food(name: str) -> Optional[dict[str, Any]]:
    """把口语食材名映射到国标表里的一条；三级都查不到返回 None（＝未收录）。

    三级顺序：精确 → 别名表 → 主名唯一回退。**不做模糊/子串匹配**——「猪油」与
    「猪肉」、「面条（挂面）」与「面条（煮）」这类一旦按子串匹配就会错配，
    错配出的数值比"未收录"更坏：它会以确定的口吻写错。
    """
    key = (name or "").strip()
    if not key:
        return None
    hit = _BY_NAME.get(key)
    if hit is not None:
        return hit
    alias = _FOOD_ALIAS.get(key)
    if alias:
        # 别名表里「鸡蛋（红皮」这种去掉右括号的写法要能命中带括号的全名
        hit = _BY_NAME.get(alias)
        if hit is None:
            for full, row in _BY_NAME.items():
                if full.startswith(alias):
                    hit = row
                    break
        if hit is not None:
            return hit
    return _BY_STEM.get(key)


def _field(item: Any, key: str) -> Any:
    """同时接受 dict 与 pydantic 模型（结构层的 Ingredient 是后者）。"""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _to_amount(value: Any) -> Optional[float]:
    """克数解析：接受 200 / "200" / "200g" / "约200克"。解析不出数字就是没有克数
    （调用方据 None 判 no_amount），**不从份数或食材类别推断**。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        m = _AMOUNT_RE.search(value)
        if m:
            try:
                num = float(m.group(1))
            except ValueError:
                return None
            return num if num > 0 else None
    return None


def _empty(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "nutrients": {
            key: {"value": None, "status": "unavailable"}
            for key, _label, _unit, _field_name in NUTRIENT_FIELDS
        },
        "ingredients": [],
        "basis": "recipe_ingredient_amounts",
        "covered": [],
        "missing": [],
        "no_amount": [],
    }


def compute_nutrition_facts(ingredients: Sequence[Any] | None) -> dict[str, Any]:
    """把食材清单算成六项营养 + 一份"哪些算进去了"的账。

    返回结构（前端直接照用，不要在前端再算一遍）：
        status       complete  全部食材已收录且都有克数
                     partial   算出了小计，但有食材未收录或缺克数
                     unavailable 一个都没算成（无食材 / 全未收录 / 全无克数）
        nutrients    {energy_kcal: {value, status}, …}，按 NUTRIENT_FIELDS 顺序
        ingredients  [{name, amount_g, lookup_name, status}]，status ∈ covered/missing/no_amount
        covered / missing / no_amount  三类食材名，供页面写清楚"这张表算的是哪几样"
    """
    items: list[tuple[str, Optional[float]]] = []
    for raw in ingredients or []:
        name = _field(raw, "name")
        name = str(name).strip() if name is not None else ""
        if not name:
            continue
        items.append((name, _to_amount(_field(raw, "amount_g"))))

    if not items:
        return _empty("unavailable")

    totals = {key: 0.0 for key, _l, _u, _f in NUTRIENT_FIELDS}
    known = {key: False for key, _l, _u, _f in NUTRIENT_FIELDS}
    rows: list[dict[str, Any]] = []
    covered: list[str] = []
    missing: list[str] = []
    no_amount: list[str] = []

    for name, amount in items:
        hit = lookup_food(name)
        if hit is None:
            missing.append(name)
            rows.append({"name": name, "amount_g": amount, "lookup_name": None, "status": "missing"})
            continue
        if amount is None:
            no_amount.append(name)
            rows.append({"name": name, "amount_g": None, "lookup_name": hit["name"], "status": "no_amount"})
            continue
        covered.append(name)
        rows.append({"name": name, "amount_g": amount, "lookup_name": hit["name"], "status": "covered"})
        ratio = amount / 100.0
        for key, _label, _unit, field_name in NUTRIENT_FIELDS:
            per100 = hit.get(field_name)
            if per100 is None:
                # 单个食材缺这一项（如国标表缺某食物的膳食纤维）不算错，只是这一项少一份贡献。
                continue
            totals[key] += per100 * ratio
            known[key] = True

    if not covered:
        status = "unavailable"
    elif missing or no_amount:
        status = "partial"
    else:
        status = "complete"

    nutrients = {}
    for key, _label, _unit, _field_name in NUTRIENT_FIELDS:
        if status == "unavailable" or not known[key]:
            # 一个食材都没算成时，即使个别字段"有值"也不报——那只是 0 的另一种写法。
            nutrients[key] = {"value": None, "status": "unavailable"}
        else:
            nutrients[key] = {"value": round(totals[key], 1), "status": "known"}

    return {
        "status": status,
        "nutrients": nutrients,
        "ingredients": rows,
        "basis": "recipe_ingredient_amounts",
        "covered": covered,
        "missing": missing,
        "no_amount": no_amount,
    }
