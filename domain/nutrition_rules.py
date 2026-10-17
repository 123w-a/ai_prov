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
# 健康灯只负责提示，不参与 hard audit。这里补上常见的“豉油”等钠来源，
# 让同一道菜不会因为模型漏填 health_lights 就显示成无钠风险。
_SODIUM_LIGHT_SEASONINGS = tuple(dict.fromkeys(
    _SALT_SEASONING + ["豉油", "蒸鱼豉油", "鱼露", "虾酱", "咸肉"]
))
_NO_SODIUM_AMOUNT_RE = re.compile(r"(?:不|无|未|没)(?:用|加|放|含|使用|额外)|零|0(?:g|克|ml|毫升)")
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
    "淡", "微量", "一点点", "不靠",
)
# 关键词**之后**出现这些词，同样说明这一句在讲「限量/警示」：
# 「盐可以几乎不放」「生抽减半」「钠极高」。
_SALT_TRAIL_QUALIFIERS = (
    "不放", "不加", "不用", "不要多", "别多放", "少放", "少用", "少加",
    "少吃", "少些", "少许", "少",
    "控制", "限制", "限量", "不超", "不超过", "低于", "以下", "降至",
    "≤", "＜", "<",
    "减半", "减量", "减少", "减",
    "钠极高", "钠很高", "钠超标", "钠高", "高钠", "含钠高",
)
# 同句内更远的限量词（不紧邻也算）：覆盖指南引用
# 「减少食盐及含钠调味品（酱油、酱类、蚝油、鸡精、味精等）」——
# 限定词与关键词之间隔着一整个并列枚举，按紧邻前缀看必然漏判。
_SALT_CLAUSE_QUALIFIERS = (
    "限量", "控制", "限制", "避免", "远离", "减少", "减", "少", "低", "无", "忌", "免",
    "取消", "省去", "省略",
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
# ---- 钠盐的「证据式」硬审计 ----
# 旧口径：盐/生抽/酱油/蚝油等调味词只要出现（且没有「少盐」这类限定语）就判违规。
# 实测（checkpoint 全库 100 条被打回的答案）这条口径把三类**正常写法**当成下料：
#   「沿锅边淋半汤匙生抽」（厨房计量不认，直接命中）、
#   「盐、生抽、香醋、蒜泥拌匀」（清单式裸提名）、
#   「盐＜5g / 控盐要求 / 本菜盐 1g、生抽 5ml 已经不多」（依据段复述）。
# 好答案因此被反复打回，前端连着闪好几次「正在形成膳食建议」。
# 新口径：调味品只有在**写出用量、折算成食盐当量后确实超量**时才算硬违规——
# 由下面的账单（`_salt_total_g`）统一判定；措辞层面的「翻案/超量」用法另行拦
# （`_salt_mention_is_risky`）。裸提名和限量说明不再触发重生成。
# 钠黄灯提示（`sodium_light_reason`）保持独立的保守口径，不受这里影响。
#
# 单位折算：全部换成「食盐当量克数」。厨房计量按常见容量估算，
# 调味品按大致含钠水平打折——15ml 生抽≈2.5g 盐、10g 蚝油≈1g 盐。
# 这是最佳努力的保守估算，只为判断「是否明显超量」，不做营养计算。
_SALT_MEASURE_AMOUNT = {
    "g": 1.0, "克": 1.0, "ml": 1.0, "毫升": 1.0,
    "汤匙": 15.0, "大勺": 15.0, "勺": 15.0,
    "茶匙": 5.0, "小勺": 5.0,
    "小撮": 0.5, "撮": 0.5,
    "少许": 0.3, "微量": 0.2, "一点点": 0.3, "些许": 0.3, "滴": 0.05,
}
# 每 g/ml（或折算后的计量）对应的食盐当量。
_SALT_G_PER_UNIT = {
    "盐": 1.0,
    "酱油": 0.17, "生抽": 0.17, "老抽": 0.17, "豉油": 0.17, "蒸鱼豉油": 0.17,
    "蚝油": 0.10,
    "鸡精": 0.50, "味精": 0.12,
    "豆瓣酱": 0.15, "辣椒酱": 0.12, "豆腐乳": 0.15, "腐乳": 0.15,
    "鱼露": 0.25, "豆豉": 0.15,
    "榨菜": 0.08, "泡菜": 0.08, "咸菜": 0.08, "酱菜": 0.08,
}
# 关键词与用量之间允许出现的填充词（「盐 整锅共 1g」「生抽（约 5ml）」）；
# 用量前还允许一个比较符（「盐＜5g」「盐 < 5g」按「至多 5g」理解）。
_SALT_AMOUNT_FILLER = r"(?:约|大约|共|合计|总共|整锅|全锅|整份|需|只|仅|各|的)*"
_SALT_AMOUNT_LEAD = r"[＜<≤]?"
_SALT_AMOUNT_UNITS = "汤匙|大勺|小勺|茶匙|小撮|撮|少许|微量|一点点|些许|滴|毫升|克|g|ml|勺"
_SALT_AMOUNT_RE_TMPL = (
    rf"(?:(?P<num>\d+(?:\.\d+)?)(?P<unit>{_SALT_AMOUNT_UNITS})"
    r"|(?P<cnum>半|小半|一|两|二|三|几)(?P<cunit>汤匙|大勺|小勺|茶匙|小撮|撮|滴|勺)"
    r"|(?P<plain>少许|微量|一点点|些许))"
)
# 用量可能写在关键词**之后**（盐 1g、生抽 2 汤匙），也可能写在**之前**
# （1 小撮盐、半勺蚝油）——两种语序都要认得出来。
_SALT_DOSE_TAIL_RE = re.compile(
    _SALT_AMOUNT_LEAD + _SALT_AMOUNT_FILLER + _SALT_AMOUNT_RE_TMPL
)
_SALT_DOSE_HEAD_RE = re.compile(
    _SALT_AMOUNT_LEAD + _SALT_AMOUNT_RE_TMPL + _SALT_AMOUNT_FILLER + r"$"
)
_SALT_CN_NUMBER = {"半": 0.5, "小半": 0.5, "一": 1.0, "两": 2.0, "二": 2.0, "三": 3.0, "几": 2.0}
# 咸菜/榨菜/泡菜/酱菜是「高钠食物」而不是可计量的调味品，写不出用量：
# 正向出现即算下料（「加榨菜炒肉丝」要拦），否定/限量语境照旧放行（「不吃咸菜」）。
_SALT_PRESERVED = {"榨菜", "泡菜", "咸菜", "酱菜"}
# 裸提名里出现这些措辞，是在承认「盐用多了」，即使没写数字也要拦。
_SALT_EXCESS_MARKERS = (
    "不少", "太多", "过多", "偏多", "很多", "大量", "大把", "多放",
    "重口", "重盐", "太咸", "很咸", "过咸",
)
# 「减盐动作」动词：紧跟在这些词后面的「不少」是在说省下来的量，
# 不是在承认自己下盐下重了（「同样咸度能省掉不少盐」「少放了不少盐」）。
_SALT_REDUCTION_VERBS = (
    "省掉", "省去", "省下", "节省", "减少", "减去", "减掉", "去掉", "去除",
    "降低", "砍掉", "少放", "少加", "少用", "少下",
)
_SALT_CUT_DOWN_TAIL_RE = re.compile(
    r"(?:" + "|".join(_SALT_REDUCTION_VERBS) + r")[了掉的]*$"
)
# 「减盐动作 + 不」紧邻收尾（「省掉不」「少放了不」）：这里的「不」属于
# 「不少」这个整体，不是在推翻前面的限量词。
_SALT_CUT_DOWN_BEFORE_REVERSAL_RE = re.compile(
    r"(?:" + "|".join(_SALT_REDUCTION_VERBS) + r")[了掉的]*不$"
)
_FORBIDDEN_QUALIFIERS = (
    "不吃", "不喝", "不放", "不加", "不含", "不要", "不用", "不必",
    "不推荐", "不建议", "别", "勿", "莫", "免", "忌", "远离", "避开",
    "回避", "少吃", "少喝", "避免", "禁用", "禁止", "杜绝", "拒绝",
    # 替代式写法：「清水代替高汤」「用无醇啤酒替代啤酒」——被替代的那一样
    # 并没有下锅，和「换成/换掉」同类。
    "代替", "替代", "顶替",
)
# 这些是「减少摄入」而不是「完全禁止」。一般病种可作为合规建议，
# 但孕期生食等硬红线不能用弱化措辞替代禁止。
_WEAK_FORBIDDEN_QUALIFIERS = (
    "少吃", "少喝", "少食", "少用", "减少", "限制", "控制", "限量", "限食",
)
_STRICT_FORBIDDEN_CONDITIONS = {"孕期"}
# 否定词与关键词之间常隔着一两个字（「别用浓肉汤」「也别加小鱼小虾类干货」
# 「不吃猪肝、腰子」），只看紧贴前缀会把这类合规写法漏判成真实下料，好答案
# 被反复打回。
# 窗口与过敏原侧的 _SAFE_NEGATION_WINDOW 对齐，且只在同一小句内生效。
_FORBIDDEN_NEGATION_WINDOW = 6
# 并列清单里的每个条目单独限长：指南引用常写成「限制动物内脏、浓肉汤、部分海鲜」，
# 整段长度远超上面的窗口，按单条目判定才不会把真下料一起放行。
_FORBIDDEN_NEGATION_ITEMS = 3
# 否定/劝阻词表：长词在前，保证「不推荐虾皮」不会被裸「不」先切掉。
# 后半段是「限量动词」——限制/控制/减少/少食这类写法语义上等价于劝阻，
# 痛风与高血压指南原文就是这么写的（「少食生冷」「限制动物内脏、浓肉汤」）。
_FORBIDDEN_NEGATION_HEAD_RE = (
    r"切勿|切莫|绝不|并不|严禁|禁止|禁用|不推荐|不建议|不能|不应|不得|不可|"
    r"不要|不用|不吃|不喝|不加|不放|不含|拒绝|杜绝|避免|避开|回避|远离|"
    r"限制|控制|减少|限量|限食|少食|少喝|少吃|少用|不宜|没有|"
    r"别|勿|莫|忌|没|无|不"
)
# 否定词后紧跟的「下料动作/程度词」属于否定本身的一部分：「也别加…」「不再放…」。
_FORBIDDEN_NEGATION_ACTION = (
    r"(?:也|还|再|就|都|要|只|仅|多|额外|继续|各种|这类|此类|用|吃|喝|加|放|下|"
    r"撒|淋|兑|拌|碰|沾|粘|选|含|靠){0,3}"
)
# 否定词到关键词之间的填充字符：不能跨标点，也不能出现「里/中/后/前/了/是/
# 不/没/无/但/却」这类另起对象或翻案的连接；下料动作同样排除，因此
# 「不加盐、放虾皮」里的「放」、「不加盐的汤里放肉汤」里的「里…放」
# 都不会被上一个小句的「不加」开脱。「的」保留（「别用久煮的肉汤」是合规劝阻）。
_FORBIDDEN_ITEM_GAP = (
    r"[^。；;！？!?\n\r，,：:了吗呢吧但却里中后前是不没无更反而"
    r"加放撒淋兑倒添拌入]"
)
_FORBIDDEN_LIST_SEP = r"[、/和与及跟]"
_FORBIDDEN_NEGATION_RE = re.compile(
    rf"(?:{_FORBIDDEN_NEGATION_HEAD_RE})"
    rf"{_FORBIDDEN_NEGATION_ACTION}"
    rf"(?:{_FORBIDDEN_ITEM_GAP}{{0,{_FORBIDDEN_NEGATION_WINDOW}}}"
    rf"(?:{_FORBIDDEN_LIST_SEP}{_FORBIDDEN_ITEM_GAP}"
    rf"{{0,{_FORBIDDEN_NEGATION_WINDOW}}})"
    rf"{{0,{_FORBIDDEN_NEGATION_ITEMS}}})$"
)
# 关键词**之后**紧跟的字样同样说明这句不是在教下料：
# 「把老火汤换成冬瓜薏米汤」「老火汤换掉」「老火汤别喝了」。
_FORBIDDEN_TRAIL_QUALIFIERS = (
    "换成", "换掉", "替换", "替代", "改成", "改为",
    "不吃", "不喝", "不放", "不加", "不用", "限量", "低于", "≤", "别", "勿", "忌",
)
_FORBIDDEN_TRAIL_LOOKAHEAD = 6
# 同形异义词的定点排除：带这些前后缀时它已经不是被点名的那种忌口食材。
#   料酒——烹饪用酒，非饮酒（肥胖规则早已声明「不用裸『酒』匹配」同一口径）；
#   啤酒——「约一啤酒瓶盖」是厨房计量参照物，不是在往锅里倒啤酒；
#   瘦肉汤——清汤，不是指南点名要限的「浓肉汤」；
#   收浓汤汁——收汁动作，不是「浓汤」。
_FORBIDDEN_EXEMPT_PREFIX = {
    "酒": ("料", "啤"),
    "肉汤": ("瘦",),
    "浓汤": ("收",),
}
_FORBIDDEN_EXEMPT_SUFFIX = {
    "啤酒": ("瓶盖",),
    "浓汤": ("汁",),
}
_REVERSED_QUALIFIERS = (
    "不", "不能", "不要", "不做", "不采用", "不接受", "拒绝",
    "没有", "没", "不是", "并非", "未",
)
# 并列组桥（E4 复测）：限定词修饰的是整个并列组，
# 「避免[生冷和生食]」「不喝[浓茶和咖啡]」「不放[味精和鸡精]」里，
# 组内后一个词的前缀只剩「组内词 + 连词」，只按紧邻前缀比对必然漏判成真下料。
_CONJUNCTIONS = ("和", "与", "跟", "及", "、", ",", "，")
# 限定词与关键词之间允许隔一个动词（「拒绝饮酒」「避免喝酒」）；
# 动词集收窄，保证「拒绝不了喝」这类反向句不被误豁免。
_VERB_BRIDGE = ("饮", "吃", "喝")
# forbidden 模式的数值让路双条件之一：「限制语 + 数字」同时出现才算合规表述。
_LIMIT_DISCLOSURE_RE = re.compile(
    r"(?:控制在|不超过|不低于|少于|小于|低于|以内|之内|以下|只放|只用|仅放|仅用|≤|<)"
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:g|克|毫升|ml|mg|毫克)")


def _qualified_by_suffix(before: str, qualifiers, series_words=()) -> bool:
    """尾部限定词匹配；限定词与关键词之间可隔一个动词（_VERB_BRIDGE），
    或隔并列组的「组内词 + 连词」序列（series_words 须来自本规则词表）。

    保留原口径：限定词前面紧跟否定词（「不少盐」「不能不加盐」）是翻案，不算限量。
    """
    # 先循环剥掉尾部的（连词 | 组内词）单元，让限定词落回尾部再比对。
    stripped = before
    while stripped:
        if stripped[-1] in _CONJUNCTIONS:
            stripped = stripped[:-1]
            continue
        peeled = False
        for word in sorted(
            (w for w in series_words if w and stripped.endswith(w)),
            key=len,
            reverse=True,
        ):
            stripped = stripped[:-len(word)]
            peeled = True
            break
        if not peeled:
            break
    for qualifier in sorted(qualifiers, key=len, reverse=True):
        if stripped.endswith(qualifier):
            preceding = stripped[:-len(qualifier)]
            if any(preceding.endswith(item) for item in _REVERSED_QUALIFIERS):
                return False
            return True
        if (
            len(stripped) >= len(qualifier) + 1
            and stripped[-1] in _VERB_BRIDGE
            and stripped[:-1].endswith(qualifier)
        ):
            preceding = stripped[:-(len(qualifier) + 1)]
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


def _salt_is_limited(compact: str, start: int, end: int, series_words=()) -> bool:
    """判断这一处盐/高钠调味料是在讲「少放/别放/减量」，还是真在下料。

    四种证据，任一成立即视为限量表述（不算违规）：
      1. 紧邻前缀限定词——保留原口径，覆盖「少盐」「不加盐」；
      2. 同句内的否定 + 下料动作——覆盖「不额外加盐」「不要用盐腌黄瓜出水」；
      3. 同句内更远的限量词、且中间没有真下料动作——
         覆盖指南引用「减少食盐及含钠调味品（酱油、蚝油…）」；
      4. 关键词后紧跟的限量/警示词——覆盖「盐可以几乎不放」「生抽减半」
         「钠极高」「盐＜5g」。

    写法里「已经写明用多少」这一支不在这里判：硬审计按「折算后的食盐当量
    是否超上限」走 `_salt_dose_near`，总当量另有数值账单（`_salt_total_g`）兜底。
    """
    clause = _clause_before(compact, start)
    cursor = len(clause)  # 关键词在 clause 内的位置（clause 恰好结束于关键词）
    # 「低钠盐/低钠酱油」仍是钠来源，不能因为前面有「低」就按限量说明放行。
    # 真正的否定（「不加低钠盐」）仍由下面的否定动作分支处理。
    low_sodium = clause.endswith("低钠")
    if not low_sodium and _qualified_by_suffix(clause, _SALT_QUALIFIERS, series_words):
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
    if not low_sodium:
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


def _amount_grams(match) -> float:
    """把一次「用量」匹配折算成毫升/克（厨房计量按常见容量估算）。"""
    if match.group("num") is not None:
        return float(match.group("num")) * _SALT_MEASURE_AMOUNT[match.group("unit")]
    if match.group("cnum") is not None:
        return (
            _SALT_CN_NUMBER[match.group("cnum")]
            * _SALT_MEASURE_AMOUNT[match.group("cunit")]
        )
    return _SALT_MEASURE_AMOUNT[match.group("plain")]


def _salt_dose_near(compact: str, start: int, end: int, keyword: str):
    """关键词紧邻处写明的用量，折算成**食盐当量**克数；没写用量则返回 None。"""
    factor = _SALT_G_PER_UNIT.get(keyword)
    if factor is None:
        return None
    tail = _SALT_DOSE_TAIL_RE.match(compact, end)
    if tail and tail.end() > end:
        return _amount_grams(tail) * factor
    head = _SALT_DOSE_HEAD_RE.search(compact[:start])
    if head:
        return _amount_grams(head) * factor
    return None


def _salt_excess_wording(compact: str, start: int, end: int) -> bool:
    """裸提名里是否出现「承认用多了」的措辞（不少/太多/重口…）。

    只在关键词前后一小段里找，三种情况不算：
      - 前面紧贴否定词（「不加太多盐」）；
      - 这一处本身在讲减盐动作（「同样咸度能省掉不少盐」「少放了不少盐」）——
        这里「不少」修饰的是省下来的量，不是在承认下料重口；
      - 中间隔了标点（窗口天然不跨句）。
    """
    for window in (compact[max(0, start - 6):start], compact[end:end + 6]):
        for marker in _SALT_EXCESS_MARKERS:
            index = window.find(marker)
            if index < 0:
                continue
            if _ends_with_reversed(window[:index]):
                continue
            if _SALT_CUT_DOWN_TAIL_RE.search(window[:index]):
                continue  # 「省掉不少盐」：减的是用量，不是承认重口
            return True
    return False


def _salt_limiter_is_reversed(compact: str, start: int) -> bool:
    """限定词本身被否定，说明这句是在承认真下料（「不做低盐版本」）。

    `_salt_is_limited` 遇到这类写法会放行（“限定词前面紧贴否定”＝翻案），
    硬审计要把它们当回真下料的证据：关键词后没有用量、也没有超量措辞时，
    由这一支兜住。

    例外：「省掉不少盐 / 减少了不少盐」里的「不少」是省下来的量，
    去掉否定词后紧邻的是减盐动作，不是翻案。
    """
    clause = _clause_before(compact, start)
    for qualifier in sorted(_SALT_QUALIFIERS, key=len, reverse=True):
        if not clause.endswith(qualifier):
            continue
        preceding = clause[:-len(qualifier)]
        if not any(preceding.endswith(item) for item in _REVERSED_QUALIFIERS):
            continue
        if _SALT_CUT_DOWN_BEFORE_REVERSAL_RE.search(preceding):
            continue  # 「省掉不少盐」：减的是用量，不是翻案
        return True
    return False


def _forbidden_is_exempt_word(compact: str, keyword: str, start: int, end: int) -> bool:
    """同形异义词：带特定前后缀时这个关键词指的不是被点名的忌口食材。

    只认定点前后缀（料酒/啤酒瓶盖/瘦肉汤/收浓汤汁），不做宽泛匹配——
    「加料酒」放行，不等于「老火汤炖猪肝」也能过。
    """
    if start > 0:
        previous = compact[start - 1]
        if previous in _FORBIDDEN_EXEMPT_PREFIX.get(keyword, ()):
            return True
    tail = compact[end:end + len("瓶盖")]
    if any(tail.startswith(suffix) for suffix in _FORBIDDEN_EXEMPT_SUFFIX.get(keyword, ())):
        return True
    return False


def _forbidden_has_trail_qualifier(compact: str, end: int) -> bool:
    """关键词后面紧跟「换成/换掉/别…」时，这一句是在讲替代或劝阻。"""
    tail = compact[end:end + _FORBIDDEN_TRAIL_LOOKAHEAD]
    for qualifier in sorted(_FORBIDDEN_TRAIL_QUALIFIERS, key=len, reverse=True):
        index = tail.find(qualifier)
        if index < 0:
            continue
        if _ends_with_reversed(tail[:index]):
            continue
        return True
    return False


def _forbidden_is_negated(
    compact: str,
    keyword: str,
    start: int,
    end: int,
    series_words=(),
    *,
    strict_reduction: bool = False,
) -> bool:
    """判断 forbidden 关键词是否处在「别用/不加/远离/限制」这类否定、劝阻语境里。

    真下料的写法（「放虾皮」「老火汤炖猪肝」）前面没有否定语境，照旧命中。
    """
    if _forbidden_is_exempt_word(compact, keyword, start, end):
        return True
    clause = _clause_before(compact, start)
    qualifiers = _FORBIDDEN_QUALIFIERS
    if strict_reduction:
        qualifiers = tuple(
            q for q in qualifiers if q not in _WEAK_FORBIDDEN_QUALIFIERS
        )
    if _qualified_by_suffix(clause, qualifiers, series_words):
        return True
    negation = _FORBIDDEN_NEGATION_RE.search(clause)
    if negation:
        if not (
            strict_reduction
            and any(
                negation.group(0).startswith(word)
                for word in _WEAK_FORBIDDEN_QUALIFIERS
            )
        ):
            return True
    return _forbidden_has_trail_qualifier(compact, end)


def _has_unqualified_occurrence(
    text: str,
    keyword: str,
    mode: str,
    series_words=(),
    *,
    salt_cap: float = None,
    strict_reduction: bool = False,
) -> bool:
    """Return True when at least one keyword occurrence expresses actual use/state.

    `mode == "salt"` 有两种口径，靠 `salt_cap` 区分：
      - `salt_cap is None`（补钠灯等提示层）：出现即算线索，保持保守；
      - 给了上限（硬审计）：只认证据——咸菜/榨菜类正向出现、写明用量且折算后
        超上限、或裸提名却带「不少/太多/重口」这类超量措辞。裸提名（「盐、生抽
        拌匀」）不再单独触发重生成，改由数值账单 `_salt_total_g` 兜底。
    """
    compact = re.sub(r"\s+", "", text or "")
    for match in re.finditer(re.escape(keyword), compact):
        start, end = match.start(), match.end()
        before = compact[max(0, start - 12):start]
        if mode == "condition" and _CONDITION_NEGATION_RE.search(before):
            continue
        after = compact[end:end + 12]
        if mode == "forbidden" and _forbidden_is_negated(
            compact,
            keyword,
            start,
            end,
            series_words,
            strict_reduction=strict_reduction,
        ):
            continue
        if mode == "salt":
            if _salt_is_limited(compact, start, end, series_words):
                continue
            if salt_cap is None:
                return True
            if _salt_limiter_is_reversed(compact, start):
                return True
            if keyword in _SALT_PRESERVED:
                return True
            dose = _salt_dose_near(compact, start, end, keyword)
            if dose is not None:
                if dose <= salt_cap:
                    continue
                return True
            # 低钠盐仍是钠来源：没有明确用量的提示必须走词级，不能被「低」豁免。
            if _clause_before(compact, start).endswith("低钠"):
                return True
            if _salt_excess_wording(compact, start, end):
                return True
            continue
        if mode == "forbidden":
            # 数值让路双条件：邻域同时有「限制语 + 数字」= 合规表述
            # （「反式脂肪控制在2克以内」）；缺一不豁免（「一天吃4克」照拦）。
            if (
                (_LIMIT_DISCLOSURE_RE.search(before) or _LIMIT_DISCLOSURE_RE.search(after[:10]))
                and (_NUMBER_RE.search(after[:12]) or _NUMBER_RE.search(before[-8:]))
            ):
                continue
        return True
    return False


def sodium_light_reason(seasonings, steps=None) -> str:
    """为一组已产出的卡片备料判断是否需要补“钠”黄灯。

    只在结构化卡片确有成盐/高钠调味料时返回理由：
    - 备料名称命中钠来源，且用量没有明确写成“不放/不用/不含”；
    - 或步骤文本里出现实际下料语境。

    `少盐` 只是烹饪取向，不能凭空伪造绿灯；只有在没有任何成盐线索时返回空串，
    由调用方保持模型原有 health_lights。
    """
    for item in seasonings or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            amount = str(item.get("amount") or "")
        else:
            try:
                name, amount = item
            except (TypeError, ValueError):
                continue
            # 兼容「dict.items() 形态」的备料项：((\"name\", \"蒸鱼豉油\"), \"5ml\")。
            # 不解这一层时 name 会变成元组字面量，reason 里直接冒出
            # 「备料含('name', '蒸鱼豉油')」—— 实现细节漏进用户可见文案（实测 10-10 Q3）。
            if isinstance(name, (tuple, list)) and len(name) >= 2:
                name = name[1]
            if isinstance(amount, (tuple, list)) and len(amount) >= 2:
                amount = amount[1]
            name = str(name or "")
            amount = str(amount or "")
        for keyword in sorted(_SODIUM_LIGHT_SEASONINGS, key=len, reverse=True):
            if keyword not in name:
                continue
            normalized_name = re.sub(r"\s+", "", name)
            normalized_amount = re.sub(r"\s+", "", amount)
            if _NO_SODIUM_AMOUNT_RE.search(normalized_amount):
                break
            return (
                f"备料含{normalized_name or keyword}，属于需要计入当日总量的钠来源；"
                "未提供具体钠含量，按保守口径标为黄灯。"
            )
    step_text = "\n".join(str(step or "") for step in (steps or []))
    for keyword in sorted(_SODIUM_LIGHT_SEASONINGS, key=len, reverse=True):
        if _has_unqualified_occurrence(step_text, keyword, "salt"):
            return (
                f"步骤中出现{keyword}等含钠调味线索，未提供具体钠含量；"
                "按保守口径标为黄灯。"
            )
    return ""


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


# 数值账单的钠源/糖源：钠源比旧版硬编码更宽——模型常用味精/鸡精顶替食盐、
# 用蜂蜜顶替白砂糖，窄词表算不到就会漏（E4 实测：7g 钠里只算得出一小部分）。
_SALT_SOURCES = _SALT_SEASONING + ["鱼露", "咸菜", "豆豉"]
_SUGAR_SOURCES = ["白砂糖", "冰糖", "麦芽糖", "蜂蜜", "红糖", "蔗糖", "糖"]


def _total_g_detail(text: str, keywords, units: str = r"g|克|ml|毫升") -> List[Dict]:
    """同 _total_g 的逐项明细：[{"item": 关键词, "grams": 克数}]（按文本区间去重）。

    长词优先 + 区间去重的语义与 _total_g 完全一致（_total_g 即对本函数求和，
    单一数据源，避免两处逻辑漂移）。
    """
    text = text or ""
    matches = []
    for kw in sorted(set(keywords), key=len, reverse=True):
        pattern = rf"{re.escape(kw)}\s*(?:约|大约)?\s*(\d+(?:\.\d+)?)\s*(?:{units})"
        for m in re.finditer(pattern, text):
            matches.append((m.start(), m.end(), float(m.group(1)), kw))
    detail: List[Dict] = []
    taken = []
    for start, end, value, kw in sorted(matches):
        if any(start < prev_end and end > prev_start for prev_start, prev_end in taken):
            continue
        taken.append((start, end))
        detail.append({"item": kw, "grams": value})
    return detail


def _total_g(text: str, keywords, units: str = r"g|克|ml|毫升") -> float:
    """尽力估算关键词后标注数量的克数总和（最佳努力，非精确）。

    长词优先并按文本区间去重："白砂糖30g"里的"糖"不再重复计入。
    """
    return sum(d["grams"] for d in _total_g_detail(text, keywords, units))


def _salt_total_g(text: str) -> float:
    """把菜谱里写明的盐/高钠调料折算成「食盐当量」克数（最佳努力，非精确）。

    与 `_total_g` 的差别：认厨房计量（汤匙/小勺/小撮/少许…）与调味品折盐
    （15ml 生抽≈2.5g 盐、10g 蚝油≈1g 盐），并逐条去重——同一份「盐 1g」
    写在原料区又在步骤区复述时只算一次，不重复求和。
    """
    return sum(d["salt_g"] for d in _salt_total_detail(text))


def _salt_total_detail(text: str) -> List[Dict]:
    """返回食盐当量账单明细：[{"item", "grams", "salt_g"}]。

    `grams` 保留原始用量折算出的克/毫升数，`salt_g` 才是用于和每日盐上限
    比较的食盐当量。长词优先、文本区间去重，保证「蒸鱼豉油」不会被「豉油」
    重复计入；相同调料的相同用量视为原料区/步骤区的复述，只算一次。
    """
    compact = re.sub(r"\s+", "", text or "")
    hits = []
    for keyword in sorted(_SALT_G_PER_UNIT, key=len, reverse=True):
        factor = _SALT_G_PER_UNIT[keyword]
        for match in re.finditer(re.escape(keyword), compact):
            start, end = match.start(), match.end()
            tail = _SALT_DOSE_TAIL_RE.match(compact, end)
            if tail and tail.end() > end:
                grams = _amount_grams(tail)
                hits.append(
                    (
                        start,
                        tail.end(),
                        keyword,
                        grams,
                        grams * factor,
                    )
                )
                continue
            head = _SALT_DOSE_HEAD_RE.search(compact[:start])
            if not head:
                continue
            grams = _amount_grams(head)
            hits.append(
                (
                    head.start(),
                    end,
                    keyword,
                    grams,
                    grams * factor,
                )
            )

    detail: List[Dict] = []
    taken = []
    counted = set()
    # 同起点时长词优先；随后按文本区间去重，避免嵌套调料词重复计数。
    for start, end, keyword, grams, salt_g in sorted(
        hits, key=lambda hit: (hit[0], -(hit[1] - hit[0]), -len(hit[2]))
    ):
        key = (keyword, round(salt_g, 4))
        if key in counted:
            continue
        if any(start < prev_end and end > prev_start for prev_start, prev_end in taken):
            continue
        counted.add(key)
        taken.append((start, end))
        detail.append({
            "item": keyword,
            "grams": round(grams, 4),
            "salt_g": round(salt_g, 4),
        })
    return detail


def audit(text: str, conditions: List[str]) -> List[Dict]:
    """审计一段菜谱/回答文本，返回所有硬禁忌命中。

    返回元素：{"condition","keyword","message","source","todo_source"?}
    """
    # None 防御：LLM 空回复 content=None 时不能把下游 re.finditer 打崩。
    text = text or ""
    violations: List[Dict] = []
    seen = set()
    for cond in conditions:
        rule = RULES.get(cond)
        if not rule:
            continue
        for kw in rule.get("forbidden", []):
            mode = "salt" if kw in _SALT_QUALIFIABLE else "forbidden"
            # 并列组桥的组内词表：salt 组内是调料（少放盐和酱油），
            # forbidden 组内是本规则禁忌词（避免生冷和生食）。
            series = _SALT_SEASONING if mode == "salt" else rule.get("forbidden", [])
            # 硬审计传上限，走「证据式」口径；补钠灯不传上限，保持保守口径。
            if _has_unqualified_occurrence(
                text,
                kw,
                mode,
                series,
                salt_cap=rule.get("salt_cap_g") if mode == "salt" else None,
                strict_reduction=cond in _STRICT_FORBIDDEN_CONDITIONS,
            ) and (cond, kw) not in seen:
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
                salt_detail = _salt_total_detail(text)
                total = sum(d["salt_g"] for d in salt_detail)
                if total > cap:
                    # 单条盐词已经触发词级命中时，也补上同一份数值账单；
                    # 汇总超量（单项都不超）则由下面的数值条单独接管。
                    for violation in violations:
                        if (
                            violation["condition"] == cond
                            and violation["keyword"] in _SALT_QUALIFIABLE
                        ):
                            violation.setdefault("detail", salt_detail)
                            violation.setdefault("cap", cap)
                    key = (cond, "高钠调料")
                    if key not in seen:
                        seen.add(key)
                        violations.append({
                            "condition": cond,
                            "keyword": f"食盐约{total:.0f}g(> {cap}g)",
                            "message": rule["message"],
                            "source": rule["source"],
                            # 数值账单：只给总量时模型会用同钠调料替换、总钠压不下来，
                            # 必须给逐项克数，模型才知道该减哪几项（E4 实测）。
                            "detail": salt_detail,
                            "cap": cap,
                        })
        # 添加糖上限检查（如肥胖：添加糖≤25g/日）
        sugar_cap = rule.get("sugar_cap_g")
        if sugar_cap:
            total = _total_g(text, _SUGAR_SOURCES, r"g|克")
            if total > sugar_cap:
                key = (cond, "添加糖")
                if key not in seen:
                    seen.add(key)
                    violations.append({
                        "condition": cond,
                        "keyword": f"糖约{total:.0f}g(> {sugar_cap}g)",
                        "message": rule["message"],
                        "source": rule["source"],
                        "detail": _total_g_detail(text, _SUGAR_SOURCES, r"g|克"),
                        "cap": sugar_cap,
                    })
    return violations


def describe(violations: List[Dict]) -> str:
    """把违禁命中格式化成给人/模型看的中文说明。"""
    if not violations:
        return "无硬禁忌命中"
    lines = []
    seen_bills = set()
    for v in violations:
        flag = " [待补权威源]" if v.get("todo_source") else ""
        lines.append(f"- {v['condition']}：命中「{v['keyword']}」→ {v['message']}（来源：{v['source']}{flag}）")
        # 数值类违禁追加账单行：逐项克数 + 差额 +「替换品类无效」的改法提示。
        # 这行直接进重生成反馈——模型要照着「减哪几项」改，而不是换个咸味调料了事。
        detail, cap = v.get("detail"), v.get("cap")
        if detail and cap:
            bill_key = (
                v["condition"],
                tuple(
                    (d["item"], round(d["grams"], 4), round(d.get("salt_g", d["grams"]), 4))
                    for d in detail
                ),
                cap,
            )
            if bill_key in seen_bills:
                continue
            seen_bills.add(bill_key)
            parts = []
            total = 0.0
            for d in detail:
                amount = d["grams"]
                salt_g = d.get("salt_g")
                part = f"{d['item']}{amount:g}g"
                if salt_g is not None:
                    total += salt_g
                    if abs(salt_g - amount) > 1e-9:
                        part += f"（≈{salt_g:g}g食盐当量）"
                else:
                    total += amount
                parts.append(part)
            lines.append(
                f"  数值账单：{' + '.join(parts)} = {total:g}g，上限 {cap:g}g，超 {total - cap:g}g；"
                "重写时按比例下调上述每一项用量——仅替换品类（如食盐换鸡精/蜂蜜）而总克数不降，"
                "本项仍会再次命中。"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    # 自检：故意塞一个痛风违禁菜单，应当被拦下
    bad = "推荐一道老火汤炖猪肝，配啤酒，饭后吃点果糖点心"
    print("conditions:", detect_conditions("我痛风又尿酸高，想吃啥"))
    vs = audit(bad, ["痛风"])
    print(describe(vs))
