"""Agent 节点轨迹观测：每次图节点执行追加一行 JSONL，供后续成本/质量优化定标。

设计约束与项目一致：写入失败一律静默，绝不阻塞对话主链路。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

_FILE = Path(__file__).resolve().parent / "data" / "agent_trace.jsonl"


def _messages_of(state) -> list:
    """兼容 dict 与 MessagesState 对象两种状态形态。"""
    messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
    return messages or []


def _chars_of(messages) -> int:
    """字符量估算：消息可为 dict、带 content 的对象或裸字符串。"""
    total = 0
    for m in messages:
        if isinstance(m, str):
            total += len(m)
        elif isinstance(m, dict):
            total += len(str(m.get("content") or ""))
        else:
            total += len(str(getattr(m, "content", "") or ""))
    return total


def _append(record: dict) -> None:
    """追加一行 JSON 记录；任何 IO 异常静默吞掉。"""
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        with _FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def trace_node(name: str):
    """装饰一个图节点函数：执行后记录节点名、耗时、进入消息数与字符量估算。"""

    def deco(fn):
        @wraps(fn)
        def wrapper(state, *args, **kwargs):
            start = time.perf_counter()
            result = fn(state, *args, **kwargs)
            try:
                messages = _messages_of(state)
                _append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "node": name,
                    "ms": int((time.perf_counter() - start) * 1000),
                    "msgs": len(messages),
                    "chars": _chars_of(messages),
                })
            except Exception:
                pass
            return result

        return wrapper

    return deco
