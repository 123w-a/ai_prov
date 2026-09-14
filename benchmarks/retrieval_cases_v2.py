"""扩充检索评测集（n=30）——用于可信的固定切法 vs 路由切法对比。

与 retrieval_cases.py / retrieval_cases_extended.py 的区别：
  1. 规模从 4 条提到 30 条，足以支撑"是否提升"的结论（原 4 条属于个例波动）；
  2. expect_keywords **不从检索结果里挖**，而是逐条用 pdfplumber 在原 PDF 中
     核验存在（见 benchmarks/verify_cases_source.py），避免"答案来自检索结果"
     的循环论证；
  3. 覆盖 kb/ 全部 5 个层级，单文档最多/最少切片两端都有代表。

字段：
  id            用例标识
  query         用户口语化问法（不照抄原文，避免字符串巧合命中）
  expect_keywords  期望出现在 top-k 文本里的关键词（原文核验过）
  source_doc    标准答案所在的权威文档
"""

RETRIEVAL_CASES_V2 = [
    # ---- 1_慢病食养指南 ----
    {"id": "htn-salt-limit", "query": "血压高的人一天吃盐不能超过多少",
     "expect_keywords": ["食盐", "钠"], "source_doc": "成人高血压食养指南（2023年版）.pdf"},
    {"id": "htn-potassium", "query": "高血压多吃点什么能帮助降血压",
     "expect_keywords": ["钾"], "source_doc": "成人高血压食养指南（2023年版）.pdf"},
    {"id": "htn-alcohol", "query": "有高血压还能不能喝酒",
     "expect_keywords": ["饮酒", "酒精"], "source_doc": "成人高血压食养指南（2023年版）.pdf"},
    {"id": "dm-staple-choice", "query": "糖尿病人主食到底怎么选",
     "expect_keywords": ["全谷物", "血糖生成指数"], "source_doc": "成人糖尿病食养指南（2023年版）.pdf"},
    {"id": "dm-sugar", "query": "糖尿病是不是一点糖都不能碰",
     "expect_keywords": ["含糖饮料", "碳水化合物"], "source_doc": "成人糖尿病食养指南（2023年版）.pdf"},
    {"id": "obesity-energy", "query": "减肥期间每天吃多少热量合适",
     "expect_keywords": ["能量"], "source_doc": "成人肥胖食养指南（2024年版）.pdf"},
    {"id": "obesity-rate", "query": "一个月减多少斤比较健康",
     "expect_keywords": ["减重"], "source_doc": "成人肥胖食养指南（2024年版）.pdf"},
    {"id": "gout-purine", "query": "痛风的人能不能吃海鲜和内脏",
     "expect_keywords": ["嘌呤"], "source_doc": "成人高尿酸血症与痛风食养指南（2024年版）.pdf"},
    {"id": "gout-water", "query": "痛风每天要喝多少水才够",
     "expect_keywords": ["饮水", "水"], "source_doc": "成人高尿酸血症与痛风食养指南（2024年版）.pdf"},
    {"id": "lipid-oil", "query": "血脂高的人炒菜用什么油比较好",
     "expect_keywords": ["脂肪", "植物油"], "source_doc": "成人高脂血症食养指南（2023年版）.pdf"},
    {"id": "ckd-protein", "query": "肾不好是不是要少吃蛋白质",
     "expect_keywords": ["蛋白质"], "source_doc": "成人慢性肾脏病食养指南（2024年版）.pdf"},
    {"id": "ckd-salt", "query": "慢性肾病患者每天盐吃多少",
     "expect_keywords": ["食盐", "钠"], "source_doc": "成人慢性肾脏病食养指南（2024年版）.pdf"},

    # ---- 2_营养素参考摄入量 DRI ----
    {"id": "dri-calcium", "query": "成年人一天需要多少钙",
     "expect_keywords": ["钙"], "source_doc": "WS_T_578.2_常量元素.pdf"},
    {"id": "dri-iron", "query": "成年女性每天要补多少铁",
     "expect_keywords": ["铁"], "source_doc": "WS_T_578.3_微量元素.pdf"},
    {"id": "dri-vitc", "query": "每天需要多少维生素C",
     "expect_keywords": ["维生素C"], "source_doc": "WS_T_578.5_水溶性维生素.pdf"},
    {"id": "dri-vitd", "query": "维生素D每天的推荐摄入量是多少",
     "expect_keywords": ["维生素D"], "source_doc": "WS_T_578.4_脂溶性维生素.pdf"},

    # ---- 3_食品标签与营养标识 ----
    {"id": "label-nrv", "query": "营养成分表里的百分比是什么意思",
     "expect_keywords": ["营养素参考值", "NRV"], "source_doc": "GB 28050-2025 预包装食品营养标签通则.pdf"},
    {"id": "label-sodium-salt", "query": "食品标签上的钠怎么换算成食盐",
     "expect_keywords": ["钠"], "source_doc": "GB 28050-2025 预包装食品营养标签通则.pdf"},
    {"id": "label-claim-lowfat", "query": "包装上写低脂是什么标准",
     "expect_keywords": ["脂肪"], "source_doc": "GB 28050-2025 预包装食品营养标签通则.pdf"},
    {"id": "canteen-guide", "query": "单位食堂怎么做才符合营养健康要求",
     "expect_keywords": ["食堂"], "source_doc": "营养健康食堂建设指南.pdf"},

    # ---- 4_特殊人群膳食指南 ----
    {"id": "preg-folic", "query": "怀孕前后要不要补叶酸",
     "expect_keywords": ["叶酸"], "source_doc": "4_备孕孕期妇女膳食指南解读_杨年红2022.pdf"},
    {"id": "preg-raw-food", "query": "孕妇能不能吃生鱼片和生鸡蛋",
     "expect_keywords": ["生"], "source_doc": "4_备孕孕期妇女膳食指南解读_杨年红2022.pdf"},
    {"id": "lactation-eat", "query": "哺乳期妈妈饮食要注意什么",
     "expect_keywords": ["哺乳期"], "source_doc": "4_哺乳期妇女膳食指南解读_杨振宇2022.pdf"},
    {"id": "infant-solid-food", "query": "宝宝几个月开始加辅食",
     "expect_keywords": ["辅食"], "source_doc": "婴幼儿喂养与膳食规范_深圳DB4403_1479172.pdf"},
    {"id": "infant-milk", "query": "婴儿母乳喂养要喂到多大",
     "expect_keywords": ["母乳"], "source_doc": "中国婴幼儿喂养状况白皮书（2022）.pdf"},
    {"id": "schoolchild-breakfast", "query": "小学生早餐应该怎么吃",
     "expect_keywords": ["早餐"], "source_doc": "中国学龄儿童膳食指南（2022）核心推荐.pdf"},
    {"id": "elderly-diet", "query": "老年人吃饭咀嚼不好怎么办",
     "expect_keywords": ["老年人", "食物"], "source_doc": "老年人膳食指导 WS-T 556-2017.pdf"},
    {"id": "student-meal", "query": "学校学生餐怎么搭配营养才够",
     "expect_keywords": ["学生餐"], "source_doc": "学生餐营养指南 WS-T 554-2017.pdf"},

    # ---- 0_总纲 ----
    {"id": "guide-salt", "query": "中国居民膳食指南建议每天吃多少盐",
     "expect_keywords": ["食盐", "盐"], "source_doc": "中国居民膳食指南（2022）.pdf"},
    {"id": "guide-milk", "query": "每天建议喝多少奶",
     "expect_keywords": ["奶"], "source_doc": "中国居民膳食指南（2022）.pdf"},
]
