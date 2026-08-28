"""Challenge retrieval cases — expect_keywords harvested from REAL top-k hits
(benchmarks/challenge_raw.json, 2026-08-27). Queries are colloquial/typo/
indirect forms users actually type. hyperthyroid-coffee was DROPPED: corpus
has no coffee/thyrotoxicosis content, so keywords cannot be harvested.
"""
EXTENDED_CASES = [
    {"id": "bp-oyster-sauce", "query": "血压高的人炒菜能多放蚝油吗", "expect_keywords": ["蚝油", "钠"]},
    {"id": "diabetes-only-wholegrain", "query": "糖尿病主食是不是只能吃粗粮", "expect_keywords": ["粗粮", "血糖"]},
    {"id": "kidney-tofu-myth", "query": "肾不好要少吃豆腐对不对", "expect_keywords": ["豆腐", "肾脏"]},
    {"id": "kid-fever-egg", "query": "孩子发烧能不能吃鸡蛋", "expect_keywords": ["鸡蛋", "辅食"]},
    {"id": "anemia-dates-typo", "query": "貧血吃紅棗管用嗎", "expect_keywords": ["大枣", "贫血"]},
]
