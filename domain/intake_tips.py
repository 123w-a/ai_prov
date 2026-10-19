"""按最近菜品的成分摄入水平生成饮食建议（确定性，不调大模型）。

为什么按「摄入水平」而不是「趋势」：
    实测近 7 天家庭数据，钠的三类趋势全是 `improving`，但黄红灯占比仍高达 61%。
    只按趋势出建议会因为「在变好」而一言不发，把真正的问题盖掉。
    这里改用**绝对占比**做判据：黄红灯占该指标样本的比例超过阈值就提。

数据来源：`data/meals.json` 每条餐的 `lights`（如 `"钠:yellow"`），
由记账链路写入，与本项目护栏同源。不读画像、不分成员——口径是「这个家最近吃什么」。

只取最近 MAX_MEALS 餐：
    测试阶段不需要把整本账跑一遍。6 餐 × 3 个指标 = 18 个灯色样本，
    对「占比」这种粗粒度判断已经够用；窗口越小，建议越贴「最近」，也越不会被
    半个月前的数据拖住。要放宽只改这一个常量。

三条纪律：
  1 每条建议都必须能追到一个真实统计量（窗口餐次、越线数、占比），追不到就不说；
  2 样本不足时明确说「样本还不够」，不拿小样本推方向性结论；
  3 不写「多吃优质蛋白」这类通用营养学套话——这个位置会被当成医嘱读。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# 只取最近这么多餐参与计算（测试阶段刻意收窄，见模块头）。
MAX_MEALS = 6
# 进展对照：最近 3 餐 vs 此前 3 餐（两段不重叠，避免自比自）。
RECENT_SPLIT = 3
# 窗口内餐次少于此值：不做任何方向性结论。
MIN_MEALS = 4
# 主窗口单项样本下限。
MIN_SAMPLES = 4
# 对照段单项样本下限（两段各 3 餐，取 3 才允许比较）。
MIN_SAMPLES_SIDE = 3

# (显示名, 阈值, 越线建议)。阈值是「黄+红」占该指标样本的上限。
# 钠卡在 50%：每日食盐 5g 的硬约束写在 RULES 里，一半餐次越线就值得提醒。
# 糖/脂肪卡在 40%：这两项容许的波动比钠宽，抓得过严会变成唠叨。
METRICS: Tuple[Tuple[str, float, str], ...] = (
    ("钠", 0.50, "今天起主动少放半勺盐，避开咸菜、腌肉这类高钠配料。"),
    ("糖", 0.40, "少碰含糖饮料和甜汤，甜味优先交给水果本味。"),
    ("脂肪", 0.40, "换蒸煮做法，炒菜油再收半勺，肥肉先放一放。"),
)

_LIGHT_RE = re.compile(r"^(?P<label>[^:]+):(?P<level>green|yellow|red)$")


def _parse_ts(meal: Dict[str, Any]) -> Optional[datetime]:
    """容错解析时间戳：一条坏时间戳不该让整块建议读不出来。"""
    raw = meal.get("ts")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _tally(meals: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """统计每个指标的绿/黄/红数量。畸形灯色（如 `"脂肪:"`）直接丢弃。"""
    tally: Dict[str, Dict[str, int]] = {}
    for meal in meals:
        for light in meal.get("lights") or []:
            m = _LIGHT_RE.match(str(light))
            if not m:
                continue
            bucket = tally.setdefault(m.group("label"), {"green": 0, "yellow": 0, "red": 0})
            bucket[m.group("level")] += 1
    return tally


def _risk_ratio(bucket: Dict[str, int]) -> Tuple[int, int, float]:
    """返回 (越线数, 总数, 越线占比)。"""
    total = bucket.get("green", 0) + bucket.get("yellow", 0) + bucket.get("red", 0)
    off = bucket.get("yellow", 0) + bucket.get("red", 0)
    return off, total, (off / total if total else 0.0)


def _pct(value: float) -> str:
    return "%d%%" % round(value * 100)


def _dish_variety(meals: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """菜式过于集中时给一条轮换建议。

    判据：**同一道菜在窗口里出现 3 次以上**。窗口只有 6 餐时，「2 次 + 1 次」
    也会占掉一半，若按占比判就会把正常的日常轮换说成「过于集中」——那是唠叨。
    3 次是「这道菜确实反复在做」的下限。集中本身不是错，只是下一轮换配料的
    机会成本最低，所以措辞是「可以换」，不是「应该少吃」。
    """
    counter: Dict[str, int] = {}
    for meal in meals:
        dish = str(meal.get("dish") or "").strip()
        if dish:
            counter[dish] = counter.get(dish, 0) + 1
    if not counter:
        return None
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    top_name, top_n = ranked[0]
    if top_n < 3:
        return None
    counted = sum(counter.values())
    share = top_n / counted if counted else 0.0
    return {
        "text": "最近 %d 餐里 %s 做了 %d 次——同一道菜反复出现，"
                "下一轮可以换一种蛋白质或深色蔬菜做搭配。" % (counted, top_name, top_n),
        "kind": "variety",
        "metric": None,
        "detail": "占窗口餐次 %s" % _pct(share),
    }


def build_intake_tips(meals: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """把记账行变成最多 3 条饮食建议。

    Args:
        meals: `data/meals.json` 的完整列表（函数内部按时间倒序取最近 MAX_MEALS 条）。
        now: 便于测试注入的当前时间（仅用于返回窗口描述，不再按天筛）。

    Returns:
        {
          "basis": "ok" | "insufficient",
          "window": {"meals": 6, "latest": "...", "earliest": "..."},
          "meals": 参与计算的餐次,
          "metrics": {指标: {"off": 越线数, "total": 样本数, "ratio": 占比}},
          "tips": [{"text", "kind", "metric", "detail"}],
        }
    """
    now = now or datetime.now()

    # 按时间倒序，取最近 MAX_MEALS 条；坏时间戳的排到最后（不丢，只是不优先）。
    ordered = [m for m in (meals or []) if isinstance(m, dict)]
    ordered.sort(key=lambda m: (_parse_ts(m) is not None, _parse_ts(m) or now), reverse=True)
    window = ordered[:MAX_MEALS]
    # 计算用时间正序，方便后面切「最近 3 餐 / 此前 3 餐」。
    window.reverse()

    counted = len(window)
    stamps = [ts for ts in (_parse_ts(m) for m in window) if ts is not None]
    payload: Dict[str, Any] = {
        "basis": "insufficient",
        "window": {
            "meals": counted,
            "latest": stamps[-1].isoformat(timespec="minutes") if stamps else None,
            "earliest": stamps[0].isoformat(timespec="minutes") if stamps else None,
        },
        "meals": counted,
        "metrics": {},
        "tips": [],
    }

    if counted < MIN_MEALS:
        payload["tips"] = [{
            "text": "最近只记到 %d 餐，样本还不够，暂时不给方向性建议。" % counted,
            "kind": "sample",
            "metric": None,
            "detail": None,
        }]
        return payload

    payload["basis"] = "ok"

    tally = _tally(window)
    latest = window[-RECENT_SPLIT:]
    prior = window[:-RECENT_SPLIT]
    latest_tally = _tally(latest)
    prior_tally = _tally(prior)

    problems: List[Dict[str, Any]] = []
    all_clear = True
    for label, threshold, advice in METRICS:
        bucket = tally.get(label)
        if not bucket:
            continue
        off, total, ratio = _risk_ratio(bucket)
        payload["metrics"][label] = {"off": off, "total": total, "ratio": round(ratio, 4)}
        if total < MIN_SAMPLES:
            all_clear = False          # 样本不够不能宣称「都稳」
            continue
        if ratio < threshold:
            continue
        all_clear = False
        problems.append({
            "text": "最近 %d 餐里有 %d 餐%s偏黄或红（%s）：%s"
                    % (counted, off, label, _pct(ratio), advice),
            "kind": "intake",
            "metric": label,
            "detail": "越线 %d / 样本 %d" % (off, total),
            "_severity": ratio / threshold,
        })

    # 越线越严重的排前面，最多两条——三条全在说「少吃」会变成唠叨。
    problems.sort(key=lambda t: -t["_severity"])
    tips: List[Dict[str, Any]] = problems[:2]

    # 进展：最近 3 餐对比此前 3 餐有明显回落，说明调整有效。两段不重叠。
    for label, _threshold, _advice in METRICS:
        if len(tips) >= 3:
            break
        prior_bucket = prior_tally.get(label)
        latest_bucket = latest_tally.get(label)
        if not prior_bucket or not latest_bucket:
            continue
        _, prior_total, prior_ratio = _risk_ratio(prior_bucket)
        _, latest_total, latest_ratio = _risk_ratio(latest_bucket)
        if prior_total < MIN_SAMPLES_SIDE or latest_total < MIN_SAMPLES_SIDE:
            continue
        if prior_ratio - latest_ratio < 0.15:     # 回落不足 15 个百分点不值得说
            continue
        tips.append({
            "text": "最近 %d 餐%s偏黄或红降到了 %s（此前 %d 餐是 %s）：方向对了，保持住。"
                    % (RECENT_SPLIT, label, _pct(latest_ratio), RECENT_SPLIT, _pct(prior_ratio)),
            "kind": "progress",
            "metric": label,
            "detail": "最近 %d 餐 vs 此前 %d 餐" % (RECENT_SPLIT, RECENT_SPLIT),
        })

    # 全绿时给一句正向反馈，避免「没问题就一个字都不说」。
    if not tips and all_clear:
        tips.append({
            "text": "最近 %d 餐的钠、糖、脂肪都在绿灯区：保持当前做法就好。" % counted,
            "kind": "positive",
            "metric": None,
            "detail": None,
        })

    # 有问题时也点出「哪些没问题」：只说「少吃什么」容易让人以为整桌都不对，
    # 而糖、脂肪实测经常是全绿的——这个事实值得说，也让人知道该守住什么。
    if problems and len(tips) < 3:
        clean = [
            label for label, _t, _a in METRICS
            if payload["metrics"].get(label)
            and payload["metrics"][label]["total"] >= MIN_SAMPLES
            and payload["metrics"][label]["ratio"] <= 0.20
        ]
        if clean:
            tips.append({
                "text": "最近 %d 餐里%s基本都在绿灯区，这几项不用改。" % (counted, "、".join(clean)),
                "kind": "positive",
                "metric": None,
                "detail": None,
            })

    variety = _dish_variety(window)
    if variety and len(tips) < 3:
        tips.append(variety)

    for tip in tips:
        tip.pop("_severity", None)
    payload["tips"] = tips[:3]
    return payload
