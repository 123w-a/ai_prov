"""餐次成员归属：记账（data/meals.json）与家庭成员对齐。

为什么需要它：周报的记账源 `meals.json` 每条只有
`ts/session/dish/lights/guardrails`，**没有成员归属**，于是"某成员本周的
达标餐次 / 已记录餐次"这个分母根本算不出来。2026-10-11 用户裁定两条加法：

  1. `record_meal()` 起把 answer 里的 `primary_member`（由 agent 图按当前生效
     成员注入）落进 `meals.json` 的**新键 `member`**；只对新餐次生效，既有键
     语义一字不改。
  2. 老餐次 best-effort 回填：**只有 `session + dish` 唯一匹配到会话 answer 时
     才写**；多解或匹配不上 → 留空 = **未归属**，不计入任何成员、绝不摊派。
     回填幂等可重复运行，并且每次都给出可审计的计数。

两条铁律：

* **菜名算法必须与 `record_meal` 逐字一致**（第一条 recipe 的 name 截 40 字符）。
  所以两边共用本模块的 `meal_dish_of()`——各写一份迟早分叉，分叉之后回填就会
  大面积匹配不上，而"匹配不上"看起来和"确实没这条"一模一样，很难发现。
* **回填只新增 `member` 一个键**：逐条 `dict(meal)` 拷贝后加键，既有键的值与顺序
  都不动（有测试逐键比对钉住）。

计数口径（脚本与只读诊断口同一套）：
    filled       本次写入 member 的条数（回填成功 N）
    already      本就有 member，幂等跳过（跳过 M）
    ambiguous    session+dish 命中多条不同答案 → 留空（未归属 K 的一部分）
    unmatched    命中 0 条 → 留空（未归属 K 的一部分）
    no_member    命中唯一一条，但那条答案自己也没记 primary_member → 留空
    unattributed = ambiguous + unmatched + no_member
"""
from __future__ import annotations

import json
from pathlib import Path

from storage.utils import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
MEALS_PATH = ROOT / "data" / "meals.json"

# 与 storage/sessions.py 的默认会话目录保持一致（sessions/*.json）。
SESSIONS_DIR = ROOT / "sessions"


def meal_dish_of(answer) -> str | None:
    """取自结构化答案的记账菜名：第一条 recipe 的 name，截 40 字符。

    与 `record_meal` 落库的算法**必须**一致，所以那个函数也调用这里。
    返回 None = 这条答案不足以上账（没有 recipes / 不是 dict）。
    """
    if not isinstance(answer, dict):
        return None
    recipes = answer.get("recipes") or []
    if not recipes:
        return None
    first = recipes[0]
    if not isinstance(first, dict):
        return None
    return str(first.get("name", "") or "")[:40]


def member_of(answer) -> str | None:
    """取自结构化答案的成员归属：`primary_member`。

    空串 / 缺键 / 非字符串 → None（= 未归属）。**不猜**：不按"当前生效成员"
    倒推、不按会话里出现过的成员认领——那会把别人的餐算到这个人的分母里。
    """
    if not isinstance(answer, dict):
        return None
    name = answer.get("primary_member")
    if not isinstance(name, str):
        return None
    return name.strip() or None


def members_of(answer) -> list[str]:
    """本餐**实际用餐人**：answer 的 `members` 数组（2026-10-15 用户裁决新增）。

    与 `member_of` 的分工——两个概念、两件事：
      · `member_of`  = 「主成员」，这一轮的健康约束按谁求解（旧键，继续用）；
      · `members_of` = 「本餐参与者」，**实际会吃这顿饭的人**；一顿饭可能只有
        两个人吃，也可能全家吃。归属统计与周报分母按它算。

    清洗：去空白、丢非字符串、**同名只留一次**（顺序即用户选择的顺序）。
    缺键 / 空数组 / 畸形 → `[]`，语义是"这一顿没登记用餐人"——**不等于**
    "没归属"：未归属由调用方按"既没有 members 也没有 member"判定。
    """
    if not isinstance(answer, dict):
        return []
    raw = answer.get("members")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name and name not in out:
            out.append(name)
    return out


def eaters_of(meal) -> list[str]:
    """从**已落库**的一条 meal 取用餐人集合（新键优先，旧键回落）。

    2026-10-15 加。`meals.json` 现在可能有两种形态：
      · 新记录：`members: ["小美", "我"]`（本餐参与者）；
      · 旧记录：`member: "小美"`（单值。当时的语义是"主成员"，但那时一顿饭
        只会记一个人，所以读的时候把它当 `[member]` 是**如实**的）。
    规则：`members` 非空就用它；否则 `member` 非空就当 `[member]`；都没有 → `[]`。

    **不猜**：不按当前 `active_id` 倒推、不按会话里出现过的成员认领——理由同
    `member_of`（那会把别人的餐算到这个人的分母里）。
    """
    if not isinstance(meal, dict):
        return []
    raw = meal.get("members")
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if name and name not in out:
                out.append(name)
        if out:
            return out
    name = str(meal.get("member") or "").strip()
    return [name] if name else []


def resolve_member(dish, answers: list[dict]) -> tuple[str | None, str]:
    """按 `session + dish` 唯一匹配，返回 `(成员名 | None, 状态)`。

    状态 ∈ matched / ambiguous / unmatched / no_member。

    唯一性按**命中条数**判（用户原话"只有唯一匹配时才写"）：命中 2 条以上一律
    留空，哪怕两条写着同一个人——多解本身就是不确定性，宁可少归一条也不摊派。
    """
    hits = [a for a in (answers or []) if meal_dish_of(a) == dish]
    if not hits:
        return None, "unmatched"
    if len(hits) > 1:
        return None, "ambiguous"
    name = member_of(hits[0])
    if not name:
        return None, "no_member"
    return name, "matched"


def collect_session_answers(sessions_dir: Path | None = None) -> dict[str, list[dict]]:
    """`session_id → 该会话所有消息的结构化答案`（纯文本/解析失败的答案不入候选池）。

    容忍坏文件：单个会话读不动就跳过，不让一条坏记录把整次回填打断。
    """
    out: dict[str, list[dict]] = {}
    for path in sorted((sessions_dir or SESSIONS_DIR).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sid = data.get("session_id") or path.stem
        answers: list[dict] = []
        for msg in data.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            raw = msg.get("answer")
            if isinstance(raw, dict):
                answers.append(raw)
                continue
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    answers.append(parsed)
        if answers:
            out[str(sid)] = answers
    return out


def backfill_meals(
    meals: list[dict], answers_by_session: dict[str, list[dict]]
) -> tuple[list[dict], dict]:
    """返回 `(新列表, 计数报告)`：只给缺 member 的餐次补 `member`，其余一字不动。"""
    report = {
        "total": len(meals),
        "filled": 0,
        "already": 0,
        "ambiguous": 0,
        "unmatched": 0,
        "no_member": 0,
    }
    out: list[dict] = []
    for meal in meals:
        if not isinstance(meal, dict):
            out.append(meal)
            report["unmatched"] += 1
            continue
        if str(meal.get("member") or "").strip():
            out.append(meal)  # 幂等：已有归属就不碰
            report["already"] += 1
            continue
        dish = meal.get("dish")
        answers = answers_by_session.get(str(meal.get("session") or ""), [])
        if isinstance(dish, str):
            name, status = resolve_member(dish, answers)
        else:
            name, status = None, "unmatched"
        if name:
            patched = dict(meal)  # 拷贝后只加一个键，既有键的值与顺序都不动
            patched["member"] = name
            out.append(patched)
            report["filled"] += 1
        else:
            out.append(meal)
            report[status] += 1
    report["unattributed"] = report["ambiguous"] + report["unmatched"] + report["no_member"]
    return out, report


def backfill_report(
    meals_path: Path | None = None,
    sessions_dir: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """跑一次回填。`dry_run=True` 只算不写（缺省；CLI 里要显式 --apply）。"""
    path = meals_path or MEALS_PATH
    try:
        meals = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        meals = []
    if not isinstance(meals, list):
        meals = []
    answers = collect_session_answers(sessions_dir)
    updated, report = backfill_meals(meals, answers)
    report["dry_run"] = dry_run
    report["sessions_scanned"] = len(answers)
    report["meals_path"] = str(path)
    if not dry_run and report["filled"]:
        # 与 record_meal 同一缩进，回填后的文件格式与平时写入的记录一致。
        atomic_write_json(path, updated, indent=1)
    return report


def attribution_stats(meals: list[dict]) -> dict:
    """只读诊断：当前记账里已归属 / 未归属的**条数**与按成员分布的**人次**。

    回填脚本每次跑完打印的是同一组数；这里让它们**任何时候都能从磁盘复算**，
    不需要额外存一份状态文件（多一份状态就多一处会和真相分叉）。

    2026-10-15 多值化：一顿饭可能有多个人吃（`members`），于是**「条数」与
    「人次」不再是同一个数**——一餐两人时 `by_member` 会加出 2，而记录只有 1 条。
    上一版是 `attributed = sum(by_member.values())`，这个等式在多值下直接崩掉。
    所以现在：`attributed` / `unattributed` 数的是**记录条数**（"归底下有没有
    归属"的现场事实），只有 `by_member` 是**人次**。两个数都要——混成一个之后，
    就再也说不清"本周记了 5 顿"和"小美吃了 6 次"到底哪个是对的。
    """
    by_member: dict[str, int] = {}
    attributed = 0
    unattributed = 0
    for meal in meals:
        eaters = eaters_of(meal)
        if not eaters:
            unattributed += 1
            continue
        attributed += 1
        for name in eaters:
            by_member[name] = by_member.get(name, 0) + 1
    return {
        "total": len(meals),
        "attributed": attributed,
        "unattributed": unattributed,
        "by_member": dict(sorted(by_member.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


