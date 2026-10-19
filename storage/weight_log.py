"""体重记录（带日期的时间序列）—— 由用户自己录入，用于算出「体重变化」。

【为什么独立成一个文件，不塞进 profile.json】

`PUT /api/profile/members/{member_id}` 是**整成员覆盖**语义（前端 `memberDraft.ts`
的注释原话：「漏掉谁就等于把谁清空」）。体重历史是一串随时间增长的点，
放在 `member.profile` 里意味着**用户下一次点「编辑」保存，整段历史就被抹掉**。
所以它单独落 `data/weight_log.json`，与 `meals.json` 同级，走同一套原子写。

【用名字归属，不用 member id】

与 `storage/meal_attribution` 的归属链保持一致（`meals.json` 的 `member`/`members`
都是名字）。另起一套 id 会多出一个可能与名字不同步的身份系统。

【一天一条】

同一天重复记录**覆盖**当天那条，不追加第二条：一天量两次体重不是"两个数据点"，
而是同一天的两次测量。覆盖后 `ts` 更新为最后一次录入的时刻，`date` 不变。

【只报变化量，不判达标】

孕期体重增长有医学推荐区间（按孕前 BMI 分档），但**本模块不引入那套判据**：
把推荐区间写进代码，就等于让一个膳食规划助手对孕期增重是否正常下结论，
而那是产检的事。本模块只回答「从最早一条到最新一条变化了多少」，
把"正不正常"留给医生。这是刻意的不作为，不是没做完。
"""
from __future__ import annotations

import json
import threading
from datetime import date as _date, datetime
from pathlib import Path

from storage.utils import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
WEIGHT_LOG_PATH = ROOT / "data" / "weight_log.json"

_LOCK = threading.RLock()

# 体重合理区间（kg）。超出这个范围的输入几乎只可能是手滑（打错小数点或单位），
# 但**不静默丢弃也不夹紧**——由调用方决定怎么提示，本层只负责不把脏数据写进历史。
MIN_KG = 20.0
MAX_KG = 400.0


def read_weight_log() -> list[dict]:
    """读全部体重记录；坏文件/不存在 → 空列表（与 read_meals 同一条容错口径）。"""
    try:
        raw = json.loads(WEIGHT_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _valid_kg(value) -> float | None:
    """把入参收成一个合法体重；不合法 → None（调用方据此拒绝，不猜、不夹紧）。"""
    try:
        kg = float(value)
    except (TypeError, ValueError):
        return None
    if kg != kg or kg in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if not (MIN_KG <= kg <= MAX_KG):
        return None
    return round(kg, 1)


def append_weight(name: str, weight_kg, day: str | None = None) -> dict:
    """记一条体重。同一天已有记录则覆盖当天那条（见模块 docstring）。

    返回写入的那条记录 `{member, date, weight_kg, ts}`。
    入参不合法（名字空 / 体重非法 / 日期格式坏）→ 抛 ValueError，**不写坏数据**。
    """
    who = str(name or "").strip()
    if not who:
        raise ValueError("缺少成员名")
    kg = _valid_kg(weight_kg)
    if kg is None:
        raise ValueError(f"体重需在 {MIN_KG:g}–{MAX_KG:g} kg 之间")

    text = str(day or "").strip()
    if text:
        try:
            _date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("日期需形如 2026-10-18") from exc
        day_text = text
    else:
        day_text = _date.today().isoformat()

    entry = {
        "member": who,
        "date": day_text,
        "weight_kg": kg,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
    }

    with _LOCK:
        rows = read_weight_log()
        kept = [
            r
            for r in rows
            if not (
                str(r.get("member") or "").strip() == who
                and str(r.get("date") or "").strip() == day_text
            )
        ]
        kept.append(entry)
        atomic_write_json(WEIGHT_LOG_PATH, kept)
    return entry


def member_weight_summary(name: str) -> dict:
    """某位成员的体重小结（单人版：薄封装，算法只在 `all_weight_summaries` 一处）。

    单独留这个入口是因为档案页与周报只关心一个人；但**计算不许抄第二遍**——
    本仓已有过「两处各写一遍、改一处漏一处」的教训。
    """
    who = str(name or "").strip()
    if not who:
        return {"member": "", "latest": None, "earliest": None,
                "change_kg": None, "count": 0, "entries": []}
    return all_weight_summaries([who])[who]


def all_weight_summaries(names: list[str]) -> dict[str, dict]:
    """一次读盘算出多位成员的小结（档案页一次要画全家，不要每人读一遍盘）。

    `change_kg` 只在**至少有两条不同日期**的记录时才有值，否则为 None：
    一条记录算不出"变化"，写 0.0 会被读成"体重没变"（与 `bmiFact` 里
    「空值不许变成 0」同一条纪律）。

    `change_kg = 最新 − 最早`，正数是增加。**不判断这个变化是否正常**，
    理由见模块 docstring。
    """
    rows = read_weight_log()
    out: dict[str, dict] = {}
    for raw in names:
        who = str(raw or "").strip()
        if not who:
            continue
        mine = [r for r in rows if str(r.get("member") or "").strip() == who]
        mine.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("ts") or "")))
        if not mine:
            out[who] = {"member": who, "latest": None, "earliest": None,
                        "change_kg": None, "count": 0, "entries": []}
            continue
        first, last = mine[0], mine[-1]
        change = None
        if first is not last and first.get("date") != last.get("date"):
            try:
                change = round(float(last["weight_kg"]) - float(first["weight_kg"]), 1)
            except (KeyError, TypeError, ValueError):
                change = None
        out[who] = {
            "member": who,
            "latest": {"date": last.get("date"), "weight_kg": last.get("weight_kg")},
            "earliest": {"date": first.get("date"), "weight_kg": first.get("weight_kg")},
            "change_kg": change,
            "count": len(mine),
            "entries": [
                {"date": r.get("date"), "weight_kg": r.get("weight_kg")} for r in mine
            ],
        }
    return out
