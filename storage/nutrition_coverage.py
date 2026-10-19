"""本周四维营养覆盖度：从菜名文本判定「这一餐有没有吃到某一类食物」。

【为什么是「覆盖度」而不是「达成率」——这条不能含糊】

`data/meals.json` 的记录形如::

    {"ts": "...", "dish": "黑椒鸡胸肉配杂粮饭与水煮西兰花",
     "lights": ["钠:green", "糖:green", "脂肪:green"], "member": "我"}

`dish` 是**自由文本菜名**：既没有食材拆分，也没有任何重量（实测 41 条去重菜名
全部如此）。没有克数就查不了成分表、也就累加不出摄入量，因此
「蛋白质吃到目标的 78%」这类**达成率在这个数据面上根本算不出来**。
给一个默认份量去凑，就是凭空造数——本仓在 `frontend/web/src/next/data/fieldFacts.ts`
里反复立过这条规矩，而档案页上就是孕期成员，编出来的营养数字会被当成医嘱。

所以本模块只回答一个**能被原文证明**的问题：
这一周，该成员有几分之一的餐**吃到了蛋白源 / 主食 / 蔬菜水果**。
判定依据是菜名里是否出现相应词，命中即计，不猜、不估、不补默认值。

【第三维「脂肪」是唯一能取实值的】

`lights` 里实测只出现过「钠 / 糖 / 脂肪」三种，没有蛋白质/碳水/维生素。
所以脂肪用现成的灯（该餐脂肪灯为 green 的比例），与
`api.routes.reports_route.is_meal_on_target` **同一套灯串解析口径**；
其余三维走文本。

【分母为什么各不相同】

「脂肪」只统计**给出了脂肪灯**的餐次（没给灯的餐次对该项是"未判定"，
塞进分母会把覆盖度压低，那是拿记账粒度当营养问题——与 `is_meal_on_target`
第 2 条细则同一条纪律）。其余三维的**所有已记录餐次**都进分母：菜名里没有
蛋白源词只说明"这餐没吃到蛋白源"，不影响分母。

【口径来源】

本模块只要**纯函数**，不导入任何 api 层模块，避免与 `reports_route` 循环依赖；
窗口与成员筛选由调用方（`reports_route`）用既有零件 `week_window` /
`eaters_of` / `read_meals` 传入，保证与周报其余数字**同源**。
"""
from __future__ import annotations

# ── 词表：由 data/nutrition_table.csv 的 category 与 41 条真实菜名共同决定 ──
#
# 设计纪律：词表只收**能明确指认该餐含有此类食物**的词。宁可漏判（该餐不算命中），
# 不可错判——漏判只是让覆盖度偏低，错判会把"没吃到蛋白"说成"吃到了"。
# 因此不收录含糊词：如「套餐」「搭配」「饭档口」这类食堂通称不含具体食材信息。

# 蛋白源：肉 / 蛋 / 水产 / 豆制品 / 奶（nutrition_table.csv 的 肉蛋/水产/豆制品/奶制品/坚果）
PROTEIN_WORDS = (
    "鸡胸", "鸡肉", "鸡丁", "鸡腿", "鸡翅", "鸡块", "鸡", "鸡蛋", "蛋清", "蒸蛋",
    "蛋花", "水煮蛋", "炒蛋", "蛋", "牛", "猪", "羊", "鸭", "鹅", "肉",
    "鱼", "虾", "蟹", "贝", "蛤", "海鲜", "三文鱼",
    "豆腐", "豆干", "豆皮", "腐竹", "豆浆", "黄豆", "毛豆",
    "牛奶", "纯牛奶", "酸奶", "奶酪",
    "花生", "核桃", "坚果", "芝麻",
)

# 主食：谷类 / 薯类（nutrition_table.csv 的 主食）
STAPLE_WORDS = (
    "米饭", "杂粮饭", "饭", "面条", "拌面", "汤面", "炒面", "面",
    "馒头", "包子", "花卷", "饺子", "馄饨", "粥", "粉", "米粉", "河粉",
    "燕麦", "面包", "吐司", "红薯", "土豆", "山药", "玉米", "年糕",
)

# 蔬菜水果（nutrition_table.csv 的 蔬果）
PRODUCE_WORDS = (
    "西兰花", "菠菜", "青菜", "白菜", "生菜", "油菜", "时蔬", "蔬菜", "菜心",
    "番茄", "西红柿", "黄瓜", "南瓜", "冬瓜", "茄子", "青椒", "辣椒", "洋葱",
    "胡萝卜", "萝卜", "豆角", "四季豆", "蘑菇", "香菇", "木耳", "菌",
    "苹果", "香蕉", "橙子", "西瓜", "梨", "葡萄", "草莓", "蓝莓", "水果",
)


def _hit(text: str, words: tuple[str, ...]) -> bool:
    """菜名里是否出现词表中的任意词。空文本一律不命中。"""
    return any(w in text for w in words if w)


def classify_dish(dish: str | None) -> dict[str, bool]:
    """把一条菜名判成「含蛋白源 / 含主食 / 含蔬果」。

    返回三项布尔值；调用方按需再叠上「脂肪灯」得到第四维。
    `dish` 为空/畸形 → 三项全 False（不命中，不抛异常）。
    """
    text = str(dish or "").strip()
    if not text:
        return {"protein": False, "staple": False, "produce": False}
    return {
        "protein": _hit(text, PROTEIN_WORDS),
        "staple": _hit(text, STAPLE_WORDS),
        "produce": _hit(text, PRODUCE_WORDS),
    }


def _fat_level(lights) -> str | None:
    """取该餐「脂肪」灯的档位；没给脂肪灯 → None（= 未判定，不进分母）。

    灯串形如 `脂肪:green`。解析口径与 reports_route.is_meal_on_target 一致
    （按最后一个冒号切分），使两处对同一条灯串的理解不会分叉。
    """
    for raw in lights or []:
        text = str(raw or "").strip()
        if not text or ":" not in text:
            continue
        name, _, level = text.rpartition(":")
        if name.strip() == "脂肪":
            return level.strip().lower() or None
    return None


def nutrition_coverage(meals: list[dict], member_name: str) -> dict:
    """按成员汇总四维覆盖度（`meals` 已由调用方筛成该成员的窗口内餐次）。

    返回结构与 `member_nutrition_progress` 对齐（`value` / `numerator` /
    `denominator`），便于前端用同一套「待补」渲染逻辑：分母为 0 → `value`
    为 **None**（不是 0.0），页面据此画空环而不是 0%，避免把"这周还没记到
    这个人的餐"读成"他一餐都没吃对"。

    `value` 取到小数点后 4 位；页面上的分子分母取自 `numerator`/`denominator`
    两个整数，所以舍入不会让页面显示的除法对不上（与既有进度字段同一条规矩）。
    """
    rows = [m for m in (meals or []) if isinstance(m, dict)]
    total = len(rows)

    hits = {"protein": 0, "staple": 0, "produce": 0}
    fat_num = fat_den = 0

    for meal in rows:
        flags = classify_dish(meal.get("dish"))
        for key in hits:
            if flags[key]:
                hits[key] += 1
        level = _fat_level(meal.get("lights"))
        if level is not None:
            fat_den += 1
            if level == "green":
                fat_num += 1

    def _pack(num: int, den: int) -> dict:
        return {
            "value": round(num / den, 4) if den else None,
            "numerator": num,
            "denominator": den,
        }

    return {
        "protein": _pack(hits["protein"], total),
        "staple": _pack(hits["staple"], total),
        "fat": _pack(fat_num, fat_den),
        "produce": _pack(hits["produce"], total),
        "meals": total,
    }
