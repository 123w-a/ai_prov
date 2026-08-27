"""Retrieval regression cases with keywords harvested from real corpus hits.

每个用例的 expect_keywords 都能在当前语料的 top-k 命中文本里找到（2026-08-27
实测 bge + 混合检索 + rerank）。它们是回归绊线：检索质量倒退导致关键词跌出
top-k 时，recall 会下跌并在此暴露。
"""

CASES = [
    {
        "id": "gout-old-soup",
        "query": "痛风能不能喝老火汤",
        "expect_keywords": ["嘌呤", "尿酸"],
    },
    {
        "id": "pregnancy-raw-fish",
        "query": "孕妇可以吃生鱼片吗",
        "expect_keywords": ["胎盘", "酮体"],
    },
    {
        "id": "hypertension-salt",
        "query": "高血压每天盐吃多少合适",
        "expect_keywords": ["盐", "钠"],
    },
    {
        "id": "diabetes-staple",
        "query": "糖尿病人主食怎么选",
        "expect_keywords": ["血糖", "全谷物"],
    },
]
