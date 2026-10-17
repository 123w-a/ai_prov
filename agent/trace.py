"""Agent 节点轨迹观测：每次图节点执行追加一行 JSONL，供后续成本/质量优化定标。

设计约束与项目一致：写入失败一律静默，绝不阻塞对话主链路。

按轮归因：每次记录都带上 ``session_id`` 与 ``turn_seq``（轮序号，由
``condense_history`` 每轮 +1 并随 checkpoint 持久化）。没有这两个字段时，
只能靠「相邻记录间隔 > 90s」猜轮次边界，离线批跑与连续快速的多轮会被并进同一段，
统计出来的「每轮调用几次」就是错的 —— 实测同一段里能被并进 61 次 structure_answer。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from infrastructure.paths import LOG_DIR


# 轨迹与用量同属观测产物，跟 app.log 一起去项目外的日志目录
_FILE = LOG_DIR / "agent_trace.jsonl"
_TRACE_LOCK = threading.Lock()
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 3


def _messages_of(state) -> list:
    """兼容 dict 与 MessagesState 对象两种状态形态。"""
    messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
    return messages or []


# 节点返回值里值得落盘做归因的标量字段（白名单）。
# 只记这几个确定的流程字段，不落模型正文，避免把用户内容写进观测文件。
_DIAG_KEYS = (
    "verify_status",
    "verify_attempts",
    "tool_calls_in_turn",
    "tool_rounds",
    "tool_budget_exhausted",
    "pick_turn_slot_granted",
    "retry_slot_granted",
)
# 违规病种清单只留标签（形如「过敏原:花生及其制品」），够定位「谁把这一轮打回的」
_MAX_VIOLATION_ITEMS = 8


def _state_field(state, key):
    """兼容 dict 与 MessagesState 对象两种状态形态取单个字段。"""
    try:
        if isinstance(state, dict):
            return state.get(key)
        return getattr(state, key, None)
    except Exception:
        return None


def _diag_of(result) -> dict:
    """从节点返回值里抽少量标量字段，回答「为什么这轮慢 / 为什么被打回」。

    只做白名单提取，拿不到就什么都不记；任何异常都不影响主链路。
    """
    if not isinstance(result, dict):
        return {}
    diag = {}
    for key in _DIAG_KEYS:
        value = result.get(key)
        if isinstance(value, (bool, int, float, str)):
            diag[key] = value
    violated = result.get("verify_violated")
    if isinstance(violated, (list, tuple)) and violated:
        diag["verify_violated"] = [
            str(item)[:60] for item in list(violated)[:_MAX_VIOLATION_ITEMS]
        ]
    return diag


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


def _rotate_if_needed() -> None:
    """超过大小上限时轮转，最多保留 ``_BACKUP_COUNT`` 份历史。"""
    try:
        if not _FILE.exists() or _FILE.stat().st_size < _MAX_BYTES:
            return
        for index in range(_BACKUP_COUNT - 1, 0, -1):
            source = _FILE.with_name(f"{_FILE.name}.{index}")
            target = _FILE.with_name(f"{_FILE.name}.{index + 1}")
            if source.exists():
                os.replace(source, target)
        os.replace(_FILE, _FILE.with_name(f"{_FILE.name}.1"))
    except Exception:
        pass


def _append(record: dict) -> None:
    """追加一行 JSON 记录；任何 IO 异常静默吞掉。"""
    try:
        with _TRACE_LOCK:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
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
                # 轮序号优先看节点自己产出的值：入口节点 condense_history 正是在本次
                # 执行里把 turn_seq 自增的，只看输入 state 会把它的记录落到上一轮。
                seq = _state_field(state, "turn_seq")
                if isinstance(result, dict) and result.get("turn_seq") is not None:
                    seq = result.get("turn_seq")
                try:
                    turn_seq = int(seq or 0)
                except (TypeError, ValueError):
                    turn_seq = 0
                record = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "node": name,
                    "session_id": str(_state_field(state, "session_id") or ""),
                    "turn_seq": turn_seq,
                    "ms": int((time.perf_counter() - start) * 1000),
                    "msgs": len(messages),
                    "chars": _chars_of(messages),
                }
                diag = _diag_of(result)
                if diag:
                    record["diag"] = diag
                _append(record)
            except Exception:
                pass
            return result

        return wrapper

    return deco


# --------------------------------------------------------------------------- #
#  Token 用量计量：没有计量就没有治理
#
#  背景：节点 trace 只记了"耗时 / 消息数 / 字符量"，看不到真实的 token 成本。
#  而 agent 的成本大头在**工具结果**与**每轮重复投喂的历史**上 —— 想优化就得先看见。
#
#  字段口径统一成和 prompt / completion 同源的四个键：
#    in_prompt / in_cached / out / total
#      其中 in_cached 是 (0, 1, 0) 里"命中的缓存读取"，它是 in_prompt 的一部分；
#      有些端点还会给 (1, 0, 0)（创建缓存），它与 (0, 1, 0) **不是**同一层，归到 in_cached 之外，
#      统一记进 "in_cache_create"，绝不做减法，避免把同源口径拆出负数。
#
#  写入沿用本模块的"绝不阻塞主链路"约定：任何异常一律静默。
# --------------------------------------------------------------------------- #

_USAGE_FILE = LOG_DIR / "usage.jsonl"
_USAGE_LOCK = threading.Lock()


def _usage_from_message(message) -> dict:
    """从一条 LLM 返回消息里抽出 token 用量；抽不到就返回 {}。"""
    usage = None
    try:
        usage = getattr(message, "usage_metadata", None)
    except Exception:
        usage = None
    if not usage:
        return {}
    try:
        in_prompt = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or (in_prompt + out))
        in_cached = 0
        in_cache_create = 0
        # details 是各家端点的差异所在：OpenAI 侧给 cached_tokens，
        # DeepSeek 侧还会给 prompt_cache_hit_tokens。取到哪个算哪个，不做加减换算。
        details = None
        for key in ("input_token_details", "input_tokens_details"):
            details = usage.get(key)
            if isinstance(details, dict):
                break
        if isinstance(details, dict):
            in_cached = int(details.get("cached_tokens") or details.get("cache_read") or 0)
            in_cache_create = int(details.get("cache_creation") or details.get("cache_write") or 0)
        return {
            "in_prompt": in_prompt,
            "in_cached": in_cached,
            "in_cache_create": in_cache_create,
            "out": out,
            "total": total,
        }
    except Exception:
        return {}


def new_turn_usage() -> dict:
    """开始一轮时调用：返回一个可累加的用量桶。"""
    return {"in_prompt": 0, "in_cached": 0, "in_cache_create": 0, "out": 0, "total": 0, "calls": 0}


def add_turn_usage(bucket: dict, message) -> dict:
    """把一条 LLM 消息的用量累加进桶；抽不到用量时只累加调用次数。"""
    if bucket is None:
        return {}
    item = _usage_from_message(message)
    if not item:
        return {}
    for key in ("in_prompt", "in_cached", "in_cache_create", "out", "total"):
        bucket[key] = int(bucket.get(key) or 0) + int(item.get(key) or 0)
    bucket["calls"] = int(bucket.get("calls") or 0) + 1
    return item


def record_turn_usage(
    bucket: dict,
    session_id: str = "",
    node: str = "",
    turn_seq: int | None = None,
) -> None:
    """把一轮的累计用量落一行 JSONL；桶为空或没有任何 LLM 调用时不写。

    ``turn_seq`` 与 ``agent_trace.jsonl`` 同源，用于把「这一轮花了多少 token」
    和「这一轮每个节点花了多少 ms」按轮对上。
    """
    try:
        if not bucket or not bucket.get("calls"):
            return
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session_id": str(session_id or ""),
            "node": str(node or ""),
            "calls": int(bucket.get("calls") or 0),
        }
        if turn_seq is not None:
            try:
                record["turn_seq"] = int(turn_seq)
            except (TypeError, ValueError):
                pass
        for key in ("in_prompt", "in_cached", "in_cache_create", "out", "total"):
            record[key] = int(bucket.get(key) or 0)
        with _USAGE_LOCK:
            _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _USAGE_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent_turn_usage(limit: int = 200) -> dict:
    """读取最近若干条用量记录，算出均值/最大值，供"成本定了没"自查。"""
    try:
        if not _USAGE_FILE.exists():
            return {"count": 0}
        lines = _USAGE_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        rows = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        if not rows:
            return {"count": 0}
        totals = [int(r.get("total") or 0) for r in rows]
        return {
            "count": len(rows),
            "avg_total": round(sum(totals) / len(totals), 1),
            "max_total": max(totals),
            "avg_calls": round(sum(int(r.get("calls") or 0) for r in rows) / len(rows), 2),
            "last": rows[-1],
        }
    except Exception:
        return {"count": 0}
