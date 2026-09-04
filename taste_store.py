"""口味信号采集与偏好建议（踩辣→辣度画像的第一环）。

设计原则：
- 词典法口味识别（可解释/零成本/可单测），不靠 LLM 猜；
- 只有「用户确认」才写入画像——自动检测只产生『建议』，零幻觉；
- 信号按 14 天滑动窗口计数，过时自动衰减。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

_LOG = Path(__file__).resolve().parent / "data" / "taste_signals.json"

# 口味词典：taste → 触发词（菜名/用户原话命中即算一票）
TASTE_LEXICON: dict[str, list[str]] = {
    "辣": ["辣", "麻辣", "水煮", "红油", "泡椒", "小米辣", "椒香", "藤椒"],
    "咸": ["咸", "腌", "腊", "酱爆", "卤", "咸菜", "豆豉"],
    "甜": ["糖醋", "拔丝", "蜜汁", "甜", "茄汁"],
    "油腻": ["炸", "肥", "油泼", "油焖", "回锅", "干锅", "红烧"],
}

# 确认写入画像时的建议话术（用户看懂、AI 可执行）
TASTE_NOTE_LABEL = {"辣": "不吃辣", "咸": "口味偏淡", "甜": "不吃甜", "油腻": "忌油腻"}


def detect_tastes(text: str) -> list[str]:
    """从菜名/原话里识别出现的口味类别（去重保序）。"""
    if not text:
        return []
    hits: list[str] = []
    for taste, words in TASTE_LEXICON.items():
        if any(w in text for w in words):
            hits.append(taste)
    return hits


def record_signals(tastes: list[str], sid: str, rec_id: int) -> None:
    """点踩时记信号：{ts, sid, rec_id, tastes}；顺带裁剪 14 天前旧事件。"""
    if not tastes:
        return
    try:
        events = json.loads(_LOG.read_text(encoding="utf-8")) if _LOG.exists() else []
    except Exception:
        events = []
    events.append(
        {"ts": datetime.now().isoformat(timespec="seconds"), "sid": sid, "rec_id": rec_id,
         "tastes": tastes}
    )
    cutoff = (datetime.now() - timedelta(days=14)).date().isoformat()
    events = [e for e in events if str(e.get("ts", ""))[:10] >= cutoff]
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def clear_signals_for(sid: str, rec_id: int) -> None:
    """清除某条被踩记录产生过的口味信号（遗忘菜品时联动清理）。"""
    try:
        events = json.loads(_LOG.read_text(encoding="utf-8")) if _LOG.exists() else []
    except Exception:
        return
    events = [e for e in events if not (e.get("sid") == sid and e.get("rec_id") == rec_id)]
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def suggest(min_count: int = 2) -> dict | None:
    """窗口内某口味被踩次数达阈值 → 返回建议 {taste, count, note_label}。"""
    try:
        events = json.loads(_LOG.read_text(encoding="utf-8")) if _LOG.exists() else []
    except Exception:
        return None
    counter: dict[str, int] = {}
    for e in events:
        for t in e.get("tastes") or []:
            counter[t] = counter.get(t, 0) + 1
    if not counter:
        return None
    top_taste, top_count = max(counter.items(), key=lambda kv: kv[1])
    if top_count < min_count:
        return None
    return {
        "taste": top_taste,
        "count": top_count,
        "note_label": TASTE_NOTE_LABEL.get(top_taste, f"少吃{top_taste}"),
    }
