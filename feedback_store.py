"""回答满意度反馈事件存储（data/answer_feedback.json）。

卡片 👍/👎 的状态在会话记录里（sessions_store.patch_message_feedback），
这里只存统计事件（完整时间戳+菜名），供周统计与推荐约束读取。
"""
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

_LOG = Path(__file__).resolve().parent / "data" / "answer_feedback.json"


def read_events() -> list[dict]:
    """读全部反馈事件；文件缺失/损坏返回空表（统计链路永不抛错）。"""
    try:
        if _LOG.exists():
            data = json.loads(_LOG.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def write_events(events: list[dict]) -> None:
    """整表覆写反馈事件（调用方已在锁内完成去重）。"""
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    _LOG.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")


def recent_down_dishes(days: int = 14, limit: int = 5) -> list[str]:
    """近 N 天被踩菜名 top（按出现次数排序），供推荐约束注入。"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    counts: Counter = Counter()
    for e in read_events():
        if e.get("rating") != "down" or str(e.get("ts", "")) < cutoff:
            continue
        dish = e.get("dish")
        if dish:
            counts[str(dish)] += 1
    return [dish for dish, _ in counts.most_common(limit)]
