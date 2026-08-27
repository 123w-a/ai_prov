"""Historical deterministic guardrail cases kept as executable regression data."""

CASES = [
    {
        "id": "gout-high-risk-menu",
        "user": "我有痛风和高尿酸",
        "answer": "老火汤炖猪肝，配啤酒，饭后吃果糖点心",
        "conditions": ["痛风"],
        "required_keywords": ["老火汤", "猪肝", "啤酒", "果糖"],
    },
    {
        "id": "hypertension-salt-cap",
        "user": "我血压高",
        "answer": "红烧肉放盐10克",
        "conditions": ["高血压"],
        "required_keywords": ["食盐约10g(> 5g)"],
    },
    {
        "id": "pregnancy-traceable-source",
        "user": "我是孕妇",
        "answer": "生鱼片配酒",
        "conditions": ["孕期"],
        "required_keywords": ["生鱼", "酒"],
    },
    {
        "id": "negated-health-profile",
        "user": "我没有糖尿病，也不是高血压",
        "answer": "清蒸鱼，全程不加盐，少盐烹饪",
        "conditions": [],
        "required_keywords": [],
    },
    {
        "id": "clean-menu-no-hit",
        "user": "我有痛风",
        "answer": "清蒸冬瓜，加姜丝，清淡少油",
        "conditions": ["痛风"],
        "required_keywords": [],
    },
]
