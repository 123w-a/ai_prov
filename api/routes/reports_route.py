"""T2-P0 饮食记录 + 周报：每次决策产出菜品时自动记账，/api/reports/weekly 聚合近7天。
数据文件 data/meals.json（JSON 数组），字段尽量少、诚实可查。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.routes.fridge_route import _PANTRY_LOG, get_fridge
from storage.meal_attribution import (
    MEALS_PATH,
    attribution_stats,
    eaters_of,
    meal_dish_of,
    member_of,
    members_of,
)
from storage.utils import atomic_write_json

router = APIRouter()
_MEALS = MEALS_PATH
_FEEDBACK_LOG = Path(__file__).resolve().parents[2] / "data" / "feedback.json"
_SUMMARY_CACHE = Path(__file__).resolve().parents[2] / "data" / "weekly_summary.json"


def read_meals() -> list[dict]:
    """读记账文件；坏文件/不存在 → 空列表（"没有数据"是诚实答案，崩掉不是）。

    周报、成员进度、只读诊断口都用它，避免"每处各写一遍 try/except"之后再各自
    长出不同的容错口径（比如一处吞坏记录、一处不吞，两个数字就对不上）。
    """
    try:
        data = json.loads(_MEALS.read_text(encoding="utf-8")) if _MEALS.exists() else []
    except Exception:
        data = []
    return data if isinstance(data, list) else []


def record_meal(
    session_id: str, answer: dict, members: list[str] | None = None
) -> None:
    """从 ChefAnswer 提取最小字段追加入库；解析失败静默跳过（不阻塞聊天）。

    2026-10-11 新增键 `member`：answer 的 `primary_member`（agent 图按当前生效
    成员注入），空/缺 → **不写这个键**（= 未归属，不摊派）。既有五个键
    （ts/session/dish/lights/guardrails）语义一字未改。
    菜名与成员都走 storage/meal_attribution 的同一套函数，保证与回填脚本
    用的是**同一算法**——两边各写一份，回填会静默地大面积匹配不上。

    2026-10-15 新增键 `members`（一顿饭不一定全家一起吃）：入参 `members` 由调用方
    从本轮请求带来（前端首屏选了谁），**缺省 None 时回落 answer 里的同名字段**，
    再缺就**不写这个键**。它与 `member` 并存、语义不同，两个都保留：
      · `member`  = 主成员（这一轮的健康约束按谁求解）；
      · `members` = 本餐实际用餐人（归属统计与周报分母按它算）。
    **不拿 `member` 去补 `members`**：那是"猜"——会把"这顿只登记了一个人"说成
    "这顿只有一个人吃"，而用户明明选了三个人。
    """
    try:
        dish = meal_dish_of(answer)
        if dish is None:
            return
        meal = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": session_id,
            "dish": dish,
            "lights": [f"{l.get('label','')}:{l.get('level','')}" for l in (answer.get("health_lights") or [])],
            "guardrails": len(answer.get("guardrails") or []),
        }
        member = member_of(answer)
        if member:
            meal["member"] = member
        eaters = [str(n).strip() for n in (members or []) if str(n).strip()]
        if not eaters:
            eaters = members_of(answer)
        if eaters:
            # 去重保序：这层不能靠前端守规矩，重复的人会把分母算重。
            seen: list[str] = []
            for name in eaters:
                if name not in seen:
                    seen.append(name)
            meal["members"] = seen
        data = read_meals()
        data.append(meal)
        atomic_write_json(_MEALS, data, indent=1)
    except Exception:
        pass


def week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """周报口径的时间窗：`now - 7 天` 到 `now`（含今天）。

    `GET /reports/weekly` 的筛选（since）与返回体里的 `range` 都用它，成员营养进度
    也用它——"这一周"全后端只能有一个口径；两处各写一遍 `timedelta(days=7)`
    迟早分叉，而分叉之后页面上两个数字会各说各话。
    """
    now = now or datetime.now()
    return now - timedelta(days=7), now


def prev_week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """**上一周**同期窗口（2026-10-15 新增，C 设计稿的「较上周」要用）。

    严格是「本周窗口整体前移 7 天」：`(now-14d, now-7d)`。不用"上周一到上周日"
    那种自然周口径——它与 `week_window` 不是同一把尺子，两把尺子量出来的差
    会在跨周界那天突然跳一下，而用户只会觉得"这个数怎么忽大忽小"。

    与 `week_window` 一样只此一处：谁要"较上周"就调它，别在别处再写一遍减法。
    """
    now = now or datetime.now()
    return now - timedelta(days=14), now - timedelta(days=7)


def _in_range(meal: dict, start: datetime, end: datetime) -> bool:
    """`start <= ts <= end`（闭区间）。坏 ts → False，绝不抛。

    与 `_in_week` 是同一套容错（那条只管下界，因为周报的上界恒为 now）。
    逐周达标率要同时卡上下界，所以单独一个；两条都必须把 `fromisoformat` 包住，
    `meals.json` 里一条坏时间戳不该让整页读不出来。
    """
    try:
        ts = datetime.fromisoformat(str(meal.get("ts") or ""))
    except (TypeError, ValueError):
        return False
    return start <= ts <= end


def is_meal_on_target(lights) -> bool:
    """单餐达标判据——**全后端只此一处**（用户 2026-10-11 裁决 (a) + 两条细则）：

      · 该餐**已给出的营养灯全为 green** → 达标；
      · 有项但不满三盏 → 有的项全绿就算达标（不得因为缺项判不达标，也不得判
        "未知"：缺项是记账的粒度问题，不是这条餐的问题）；
      · **一条灯都没有** → 不算达标，但**仍计入分母**（"已记录但未判达标"；
        把它从分母里剔掉会让进度虚高——剔除等于给没数据的餐次发满分）；
      · `guardrails` **不参与判据**：它的语义是"禁忌是否命中"，不是"营养是否
        达标"（审计结论，用户已认可）。

    灯串形如 `钠:green`；取最后一个冒号之后的部分当档位（`绿:green` 也认）；
    畸形串（没有冒号、空档位）按"不是 green"处理——宁可不达标，不假装达标。
    """
    levels: list[str] = []
    for raw in lights or []:
        text = str(raw or "").strip()
        if not text:
            continue
        levels.append(text.rsplit(":", 1)[-1].strip().lower())
    if not levels:
        return False
    return all(level == "green" for level in levels)


def member_nutrition_progress(
    member: dict | None = None,
    meals: list[dict] | None = None,
    now: datetime | None = None,
) -> dict:
    """成员营养进度：本窗口内该成员 **达标餐次 ÷ 已记录餐次**。

    用户 2026-10-11 裁定的定义（原样落在这一处，别在别处再解释一遍）：
      · 周期 = 现有周报使用的同一周（`week_window`，与 /reports/weekly 的 range 同源）；
      · 分子 = 该成员本周**达标**餐次（判据见 `is_meal_on_target`：该餐营养灯全绿），
        分母 = 该成员本周**已记录**餐次；分母为 0 → value **null**（不返回 0%：
        "0/0" 看着像"一餐都没达标"，其实是"这周还没记到他的餐"）；
      · 分子分母都只统计**已归属到该成员**的餐次——未归属的餐次对任何成员都不算，
        绝不摊派（归属由 record_meal 落键、老数据经 scripts/backfill_meal_members.py
        在 session+dish 唯一匹配时才回填）。

    `meals`/`now` 可注入（测试与批量调用）：一次 payload 里 8 位成员不要读 8 遍盘。

    值取到小数点后 4 位（2/3 → 0.6667）；页面上"3/5 · 60%"里的分子分母取自
    numerator/denominator 这两个整数，所以舍入不会让页面上的除法对不上。
    """
    start, end = week_window(now)
    period = {"start": start.date().isoformat(), "end": end.date().isoformat()}
    name = str((member or {}).get("name") or "").strip()
    if not name:
        # 连"是谁"都不知道 → 三个值都 null（不是 0/0：那是"没记到餐"，语义不同）。
        return {"value": None, "numerator": None, "denominator": None, "period": period}

    rows = read_meals() if meals is None else meals
    mine: list[dict] = []
    for meal in rows:
        if not isinstance(meal, dict):
            continue
        if name not in eaters_of(meal):
            continue  # 他没参与这一顿、以及未归属的餐，都不进这个人的分母
        try:
            ts = datetime.fromisoformat(str(meal.get("ts") or ""))
        except Exception:
            continue  # ts 坏掉的记录连窗口都判不了，不进分母（也不冒充达标）
        if ts >= start:
            mine.append(meal)

    denominator = len(mine)
    if denominator == 0:
        return {"value": None, "numerator": 0, "denominator": 0, "period": period}
    numerator = sum(1 for m in mine if is_meal_on_target(m.get("lights")))
    return {
        "value": round(numerator / denominator, 4),
        "numerator": numerator,
        "denominator": denominator,
        "period": period,
    }


@router.get("/reports/member-weekly")
def member_weekly_report(name: str = ""):
    """某位成员的近 7 天周报（2026-10-15 新增，多值用餐人配套）。

    为什么要单开一口：`/reports/weekly` 的口径是**家庭整体**——它把一周里
    所有的餐都算进去。而成员详情要回答的是另一个问题：「**这位成员**这一周
    吃得怎么样」。两者不是同一份数据的两种排版：多位成员一起吃的餐会同时
    进多个人各自的分母，所以单人视角的数字加起来**不等于**家庭视角。

    实现方式刻意是「先筛餐、再复用」，而不是另写一套算法：
      · `_light_series` / `_light_trends` / `_weekly_recommendations` 本来就
        接收"一串餐"，把筛好的 `mine` 喂进去即可；
      · 达标率走既有的 `member_nutrition_progress`，逐餐明细走既有的
        `_meals_detail`。
    这样"家庭版"和"成员版"在**同一个算法**上算出来，不会出现同一份数据两个
    界面给出不同判断（本仓已有过这类教训）；将来改判据也只有一处。

    `name` 用**名字**而不是 id：整条归属链（`meals.json` 的 `member`/`members`、
    `by_member` 的键、回填脚本的匹配）都是以名字为准的，这里另起一套 id 会多出
    一个可能与名字不同步的身份系统。前端直接用 `FamilyMemberRow.name` 传进来。

    查不到这位成员的餐时**不报错**：返回 `has_data: false` 与一句诚实说明。
    名字陌生（比如成员改过名、或有人手拼了 URL）也走同一条路——它同样是
    "没有这位成员的餐"，而不是一个需要 500 的异常。
    """
    who = str(name or "").strip()
    data = read_meals()
    since, now = week_window()
    period = {"start": since.date().isoformat(), "end": (since + timedelta(days=7)).date().isoformat()}
    days = [(datetime.now().date() - timedelta(days=6 - i)).isoformat() for i in range(7)]

    if not who:
        return {
            "name": "",
            "has_data": False,
            "message": "没有指定要看哪一位成员。",
            "period": period,
            "days": days,
        }

    # 只保留窗口内、且这位成员**确实参与了**的餐（`eaters_of` 新键优先、旧键回落）。
    recent = [m for m in data if isinstance(m, dict) and _in_week(m, since)]
    mine = [m for m in recent if who in eaters_of(m)]

    if not mine:
        return {
            "name": who,
            "has_data": False,
            # 与周报页同一条诚实纪律：没记到餐就直说，不拿 0% 冒充"一餐都没达标"。
            "message": f"近 7 天还没有记到 {who} 的餐次。这一顿是谁吃的，在首屏选一下就会记下来。",
            "period": period,
            "days": days,
            "meals": 0,
            "progress": member_nutrition_progress({"name": who}, meals=[], now=now),
            "light_series": {},
            "light_trends": {},
            "dishes": [],
            "dish_images": {},
            "recommendations": [],
            "meals_detail": [],
        }

    dishes = Counter(m["dish"] for m in mine if m.get("dish"))
    top = dishes.most_common()
    trends = _light_trends(mine, since)
    return {
        "name": who,
        "has_data": True,
        "message": None,
        "period": period,
        "days": days,
        "meals": len(mine),
        "progress": member_nutrition_progress({"name": who}, meals=mine, now=now),
        # 与家庭版同一批函数、同一套窗口：只是入参换成了"这位成员的餐"。
        "light_series": _light_series(mine),
        "light_trends": trends,
        "dishes": top,
        "dish_images": _dish_images([d for d, _ in top]),
        "recommendations": _weekly_recommendations(top, trends),
        # 逐餐明细带 `members`：前端能显示"这一顿还有谁一起吃"。
        "meals_detail": _meals_detail(mine),
    }


@router.get("/reports/meal-attribution")
def meal_attribution_diag():
    """只读诊断口：记账里的**成员归属现状**（老餐次回填的可审计计数）。

    `scripts/backfill_meal_members.py` 每次运行都会打印同一组口径的数；这个口让
    它们**任何时候都能从磁盘复算**——不额外存一份"上次回填结果"的状态文件，
    多一份状态就多一处会和真相分叉（本仓已有过这类教训）。

    字段：total / attributed / unattributed / by_member（按条数降序），
    外加本周窗口内的 attributed_this_week / unattributed_this_week——分子分母
    只认本周且只认已归属，这两个数就是"分母能不能算出来"的现场事实。
    """
    meals = read_meals()
    start, end = week_window()
    in_week = [
        m for m in meals
        if isinstance(m, dict)
        and str(m.get("ts") or "")[:10] >= start.date().isoformat()
    ]
    week_attributed = sum(1 for m in in_week if eaters_of(m))
    return {
        **attribution_stats(meals),
        "period": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "attributed_this_week": week_attributed,
        "unattributed_this_week": len(in_week) - week_attributed,
    }


def _member_hint(who: str, mine: list[dict]) -> str:
    """这位成员本周**最值得说的一句话**（周报成员卡的「需要注意」）。2026-10-15 新增。

    只讲能从他自己的记录里**真算出来**的事：红灯 > 全达标 > 持续黄 > 没给灯。
    绝不编造"晚餐蔬菜偏少""全谷物不足"这类需要**食材与餐段**才能下的判断——
    记账行里只有 `ts / dish / lights / guardrails`，没有食材表也没有餐段字段，
    写出来就是无源之词（本项目铁律：不许补造）。

    优先级是刻意的：有红灯先讲红灯（那是唯一需要马上改的事）；一条红都没有又
    全部达标，就明说保持；否则挑出现最多的那盏黄灯。最后那条兜底覆盖"记了餐
    但没给营养灯"——那不是坏数据，是记账粒度粗，说出来比沉默有用。
    """
    if not mine:
        return "这一周还没有他的饮食记录。记一笔，才能替他盯住营养。"
    total = len(mine)
    on_target = sum(1 for m in mine if is_meal_on_target(m.get("lights")))
    reds: Counter = Counter()
    yellows: Counter = Counter()
    for m in mine:
        for raw in m.get("lights") or []:
            text = str(raw or "").strip()
            if not text:
                continue
            nutrient, _, level = text.rpartition(":")
            level = level.strip().lower()
            if not nutrient.strip():
                continue
            if level == "red":
                reds[nutrient.strip()] += 1
            elif level == "yellow":
                yellows[nutrient.strip()] += 1
    if reds:
        nutrient, count = reds.most_common(1)[0]
        return f"这一周有 {count} 顿的{nutrient}偏高（红灯），做菜时先从这里收一收。"
    if not yellows and on_target == total:
        return "这一周每一顿都达标，保持这个做法。"
    if yellows:
        nutrient, count = yellows.most_common(1)[0]
        return f"这一周有 {count} 顿的{nutrient}是基本达标，还没到绿灯，再多留意一点。"
    return f"这一周 {total} 顿里有 {total - on_target} 顿没有营养灯，补一下才好判断。"


def _member_week_rows(recent: list[dict], prev_recent: list[dict] | None = None) -> list[dict]:
    """每位成员本周的一行摘要（周报页的成员卡片用）。2026-10-15 新增。

    为什么要后端算而不是让前端拿 N 次 `/reports/member-weekly`：卡片上的数字必须
    与点进去看到的那份详情**同源**。前端各拉一次也能得到数字，但那会在两处各留一份
    "本周这位成员吃了几顿"的算法——改判据时漏改一处，卡片说 5 顿、详情列 4 条，
    而两边单看都自洽（本仓已有过这类教训）。这里与 `/member-weekly` 复用同一批零件
    （`eaters_of` / `is_meal_on_target` / 同一份 Counter 口径），只有入参不同。

    成员名单取自 `profile.json`（家里有谁），**不是**从 meals 里出现过的名字反推：
    一位本周一顿都没记到的成员仍然要有卡片、显示"这一周还没记到"，从列表里消失
    会被读成"这个人不在这个家里"。反过来，档案里删掉的成员自然不再出现。

    `lights` 与家庭版**同构**（复合键 `"钠:green"` 的计数），前端复用同一个 parseLights
    拆解——两处若各拆一遍，"某些营养素有几种颜色"这件事就有两个实现。

    `shared` = 这一周有几顿是**跟人一起吃**的。它只在多人餐上才不为 0，
    用于在卡片上说清"这个人的餐有一部分是共同用餐"——共同用餐按共同要求判定，
    数字与独食不可直接比（见 `is_meal_on_target` 的多人语义）。
    """
    from api.routes.preferences_route import _read_family  # 函数内导入：避免模块级循环依赖

    family = _read_family()
    if not family:
        return []
    rows: list[dict] = []
    for m in family.get("members") or []:
        if not isinstance(m, dict):
            continue
        who = str(m.get("name") or "").strip()
        if not who:
            continue
        mine = [x for x in recent if who in eaters_of(x)]
        before = [x for x in (prev_recent or []) if who in eaters_of(x)]
        lights = Counter(l for x in mine for l in (x.get("lights") or []))
        rows.append({
            "name": who,
            "meals": len(mine),
            # 达标数与分母用全后端同一条判据（is_meal_on_target），不再写第二套。
            "on_target": sum(1 for x in mine if is_meal_on_target(x.get("lights"))),
            "lights": dict(lights),
            "shared": sum(1 for x in mine if len(eaters_of(x)) > 1),
            # 「较上周」（2026-10-15）：上周同期该成员**自己的**餐数与达标数，
            # 同样只算他参与的餐（同一把 eater 尺子），不与家庭数混用。
            # 两个都是 0 = 上周没有他的记录，前端要显示「上周无记录」——
            # 拿 0 当"上周一餐都没达标"是把"不知道"说成"很差"，本项目禁止。
            "prev_meals": len(before),
            "prev_on_target": sum(1 for x in before if is_meal_on_target(x.get("lights"))),
            "hint": _member_hint(who, mine),
        })
    return rows


def _week_rate_series(data: list[dict], now: datetime | None = None, weeks: int = 5) -> list[dict]:
    """最近 N 周的**达标率**序列（周报「趋势」折线用）。2026-10-15 新增。

    与 `_light_series` **不是一回事，别互相顶替**：`_light_series` 是近 7 天**逐日**
    的营养灯档位（画"这几天吃得好不好"）；这里是**逐周**达标率（画"这几周在往哪走"）。
    C 设计稿要的是后者——纵轴 0–100%、节点上写百分比、末尾一个大号本周值。

    最旧一周在前、本周在最后（横轴由左至右是时间）。某周零记录时 `rate` 是
    **null 而不是 0**：折线必须在那里**断开**，不能连到 0% —— 那会画出一条
    "那周吃得极差"的假谷底，而真相是"那周没记"（本项目铁律：不许补造）。

    `is_current` 让前端标出本周，免得再靠"最后一个就是本周"这种隐式约定。
    """
    now = now or datetime.now()
    out: list[dict] = []
    for back in range(weeks - 1, -1, -1):
        end = now - timedelta(days=7 * back)
        start = end - timedelta(days=7)
        rows = [m for m in data if isinstance(m, dict) and _in_range(m, start, end)]
        on_target = sum(1 for m in rows if is_meal_on_target(m.get("lights")))
        out.append({
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            # 轴标签只给「月/日-月/日」：横轴放不下年份，而这张图最多看 5 周。
            "label": f"{start.month}/{start.day}-{end.month}/{end.day}",
            "meals": len(rows),
            "on_target": on_target,
            "rate": round(on_target / len(rows), 4) if rows else None,
            "is_current": back == 0,
        })
    return out


@router.get("/reports/weekly")
def weekly_report():
    """近 7 天聚合：餐数、菜品 Top、红绿灯计数、护栏触发次数。空数据返回诚实提示。"""
    data = read_meals()
    since, now = week_window()
    # 走 `_in_week` 而不是就地 fromisoformat：`meals.json` 里任何一条坏 `ts`
    # （手工编辑过、或早期格式）原本会让整个周报 500 —— 一条坏记录不该让整页读不出来。
    # 这个包装本来只长在诊断口与成员口上，周报自己漏了，2026-10-15 补齐。
    recent = [m for m in data if isinstance(m, dict) and _in_week(m, since)]
    if not recent:
        return {"has_data": False, "message": "近 7 天还没有饮食决策记录。去聊一道菜吧，吃完自动记一笔。"}
    # 「较上周」（2026-10-15）：上周同期窗口，只用来做对比，不进任何其它统计。
    prev_since, prev_end = prev_week_window(now)
    prev_recent = [
        m for m in data
        if isinstance(m, dict) and _in_range(m, prev_since, prev_end)
    ]
    prev_on_target = sum(1 for m in prev_recent if is_meal_on_target(m.get("lights")))
    here_on_target = sum(1 for m in recent if is_meal_on_target(m.get("lights")))
    dishes = Counter(m["dish"] for m in recent if m.get("dish"))
    lights = Counter(l for m in recent for l in m.get("lights", []))
    top = dishes.most_common(5)
    return {
        "has_data": True,
        "meals": len(recent),
        # 逐餐明细（2026-10-14 新增）：周报页要做「点一道菜 → 回到那一餐」，
        # 而 `meals` 只是一个整数，前端手里没有"哪一餐"可点。
        # 与 top_dishes **并行**给出、不改它的结构——理由同下面 light_series 那段：
        # top_dishes 有多处消费者（agent_tools / 旧周报页 / ChatArea / next 周报页），
        # 把它的类型从"计数"改成"列表"等于四处一起改契约。
        "meals_detail": _meals_detail(recent),
        "top_dishes": top,
        # 2026-10-10 周报方向稿（用户第 3 条「要数据真实」）：趋势图与菜品配图
        # 都要有真数据来源。两个新字段与 top_dishes **并行**给出，不改它的结构——
        # top_dishes 有四处消费者（agent_tools / 旧 WeeklyReportPage / ChatArea /
        # next WeeklyView），改结构等于四处一起改契约，收益不值这个风险。
        "light_series": _light_series(recent),
        "dish_images": _dish_images([name for name, _ in top]),
        "lights": dict(lights),
        "light_trends": _light_trends(recent, since),
        "guardrail_triggers": sum(m.get("guardrails", 0) for m in recent),
        "range": [since.date().isoformat(), now.date().isoformat()],
        # 成员卡片（2026-10-15）：与家庭整体那段**并列**给出，不是它的子级——
        # 「家庭」与「每位成员」是同级对象，家庭不是"默认视图"、成员也不是下钻。
        # 空周不返回这个键（整页已是空态，没有卡片可言）。
        "members": _member_week_rows(recent, prev_recent),
        # 「较上周」（2026-10-15，C 设计稿的「本周餐次 21 餐 / 较上周 +3 餐」）：
        # 上周同期整户的餐数与达标数。rate 在上周零记录时是 **null**，
        # 前端要显示「上周无记录」而不是「+0」——上次没记不是"没有变化"。
        "prev": {
            "meals": len(prev_recent),
            "on_target": prev_on_target,
            "rate": round(prev_on_target / len(prev_recent), 4) if prev_recent else None,
            "range": [prev_since.date().isoformat(), prev_end.date().isoformat()],
        },
        # 本周达标率：与 rate_series 里 is_current 那格**同一个算法**（都走
        # is_meal_on_target 与同一个分母），这里只是平铺出来给卡片直接用。
        "rate": round(here_on_target / len(recent), 4) if recent else None,
        # 逐周达标率（C 设计稿「趋势」那张折线）：与 light_series 并行给出、
        # 不改它的结构，理由同上面 top_dishes 那段——它有多处消费者。
        "rate_series": _week_rate_series(data, now),
        "recommendations": _weekly_recommendations(top, _light_trends(recent, since))
                             + _pantry_restock_tips(_read_pantry_log(), set(get_fridge().get("items", [])))
                             + _feedback_tips(_read_feedback_log()),
        "feedback_summary": _feedback_summary(_read_feedback_log()),
    }


def _meals_detail(recent: list[dict]) -> list[dict]:
    """近 7 天逐餐明细，时间倒序（2026-10-14 新增）。

    周报页此前只能用 `top_dishes` 的「菜名 + 次数」，点一道菜回不到"哪一餐"；
    这里把记账行本身摊开，前端才有可点的目标。

    每个字段都来自 `data/meals.json` 的记账行，**不做额外推断**：
      · `dish` 是记账里那道菜的原名，不改写、不截断（要不要省略号是前端的事）；
      · `lights` 拆成 `{nutrient, level}`——记账里是 `"钠:green"` 这种「标签:档位」串。
        取**最后一个**冒号切分（与 `is_meal_on_target` 同一条规则，兼容 `"绿:green"`）；
        没有冒号或切不出标签的脏值跳过，不猜档位；
      · `member` 为空表示旧记录没有成员归属 → null，前端据此不显示成员，
        不替它写成"全家"（那是把"不知道"说成"确定"）；
      · `on_target` 直接复用 `is_meal_on_target`，不在这里另写一套判据——
        "这一餐达标没有"全后端只能有一个算法。
    """
    rows: list[dict] = []
    for m in sorted(recent, key=lambda x: str(x.get("ts") or ""), reverse=True):
        lights: list[dict] = []
        for raw in m.get("lights") or []:
            text = str(raw or "").strip()
            if ":" not in text:
                continue
            label, level = text.rsplit(":", 1)
            if label.strip():
                lights.append({"nutrient": label.strip(), "level": level.strip().lower()})
        rows.append({
            "ts": m.get("ts") or "",
            "dish": m.get("dish") or "",
            "member": m.get("member") or None,
            # 本餐实际用餐人（2026-10-15）。旧记录只有单值 `member` 时这里回落成
            # 单元素数组——如实表达"我们只登记到一个人"，而不是"当时只有一个人吃"。
            # 前端要区分这两种情况时看 `member` 有没有值，不要靠数组长度猜。
            "members": eaters_of(m),
            "lights": lights,
            "guardrails": m.get("guardrails") or 0,
            # 回原会话的定位信息：让前端以后能"回到那一轮"，本轮先如实带上。
            "session": m.get("session") or None,
            "on_target": is_meal_on_target(m.get("lights")),
        })
    return rows


def _in_week(meal: dict, since: datetime) -> bool:
    """这条记录是否落在周窗口内；`ts` 坏掉时**算不进来**，也不猜。

    单独拎出来是因为 `/reports/weekly`、亲属口 (`/reports/member-weekly`) 与
    诊断口各有自己的一段筛选，此前是各写一遍 `datetime.fromisoformat(...) >= since`。
    坏 `ts` 会抛 ValueError，在有的地方被 try 包住、有的地方没有——那种不一致
    迟早会在某个入口上变成 500。
    """
    try:
        return datetime.fromisoformat(str(meal.get("ts") or "")) >= since
    except Exception:
        return False


def _light_series(recent: list[dict]) -> dict[str, list[float | None]]:
    """近 7 天逐日绿灯占比——周报趋势 sparkline 的真数据（方向稿要求）。

    值 = 当日该营养素的绿灯数 ÷ 当日该营养素灯数（0..1，保留 3 位）。
    当日无该营养素观测 → None：前端**断线不插值**，插值等于替后端编数据
    （与「数据不足不画趋势线」同一条诚实纪律）。
    天窗口取含今天的最后 7 天（today-6 .. today）；since 窗口外的当天残段
    归不进任何天槽，直接跳过——窗口口径与 _light_trends 一致。
    """
    today = datetime.now().date()
    days = [(today - timedelta(days=6 - i)).isoformat() for i in range(7)]
    day_index = {d: i for i, d in enumerate(days)}
    per: dict[str, list[dict[str, int]]] = {}
    for meal in recent:
        idx = day_index.get(str(meal.get("ts", ""))[:10])
        if idx is None:
            continue
        for raw in meal.get("lights", []):
            if ":" not in raw:
                continue
            label, level = raw.rsplit(":", 1)
            bucket = per.setdefault(label, [{} for _ in days])[idx]
            bucket[level] = bucket.get(level, 0) + 1
    series: dict[str, list[float | None]] = {}
    for label, slots in per.items():
        values: list[float | None] = []
        for bucket in slots:
            total = sum(bucket.values())
            values.append(round(bucket.get("green", 0) / total, 3) if total else None)
        series[label] = values
    return series


def _dish_images(names: list[str]) -> dict[str, str]:
    """top 菜品的成品图（dish_assets 里已有才给；没有的菜不出现在这张表里，
    前端据此渲染占位——不猜、不拼不存在的地址）。"""
    out: dict[str, str] = {}
    try:
        from storage.dish_assets import find_dish_asset
    except Exception:
        return out
    for name in names:
        try:
            asset = find_dish_asset(name)
        except Exception:
            asset = None
        url = (asset or {}).get("image_url")
        if isinstance(url, str) and url:
            out[name] = url
    return out


def _light_trends(recent: list[dict], since: datetime) -> dict[str, str]:
    """Compare risk-light ratios in the latest three days with the prior four.

    A risk light is yellow or red. Trends require at least two observations in
    each window so sparse history is shown honestly instead of over-interpreted.
    """
    split = since + timedelta(days=4)
    windows: dict[str, list[str]] = {"previous": [], "latest": []}
    for meal in recent:
        bucket = "latest" if datetime.fromisoformat(meal["ts"]) >= split else "previous"
        windows[bucket].extend(meal.get("lights", []))

    labels = {light.rsplit(":", 1)[0] for values in windows.values() for light in values if ":" in light}
    trends: dict[str, str] = {}
    for label in labels:
        previous = [light.rsplit(":", 1)[1] for light in windows["previous"] if light.startswith(label + ":")]
        latest = [light.rsplit(":", 1)[1] for light in windows["latest"] if light.startswith(label + ":")]
        if len(previous) < 2 or len(latest) < 2:
            trends[label] = "insufficient"
            continue
        previous_risk = sum(level in {"yellow", "red"} for level in previous) / len(previous)
        latest_risk = sum(level in {"yellow", "red"} for level in latest) / len(latest)
        if latest_risk < previous_risk:
            trends[label] = "improving"
        elif latest_risk > previous_risk:
            trends[label] = "worsening"
        else:
            trends[label] = "stable"
    return trends


_TREND_TIPS = {
    "钠": "近几餐钠风险抬头：主动少放半勺盐，避开咸菜、腌肉这类高钠配料。",
    "糖": "近几餐糖风险抬头：少碰含糖饮料和甜汤，甜味优先交给水果本味。",
    "脂肪": "近几餐脂肪风险抬头：换蒸煮做法，炒菜油再收半勺，肥肉先放一放。",
}


def _weekly_recommendations(top_dishes: list, light_trends: dict[str, str]) -> list[str]:
    """Turn observed weekly evidence into a few honest suggestions.

    Every suggestion must trace back to a real signal (trend or dish mix);
    sparse data gets no directional advice.
    """
    tips: list[str] = []
    for label, trend in light_trends.items():
        if trend == "worsening" and label in _TREND_TIPS:
            tips.append(_TREND_TIPS[label])
    if top_dishes and not any(t == "insufficient" for t in light_trends.values()):
        names = "、".join(d for d, _ in top_dishes[:2])
        tips.append(f"这周常吃{names}；维持当前口味的同时，下一周可以换一种蛋白质或深色蔬菜做搭配。")
    return tips


def _read_pantry_log() -> list[dict]:
    try:
        return json.loads(_PANTRY_LOG.read_text(encoding="utf-8")) if _PANTRY_LOG.exists() else []
    except Exception:
        return []


def _read_feedback_log() -> list[dict]:
    try:
        return json.loads(_FEEDBACK_LOG.read_text(encoding="utf-8")) if _FEEDBACK_LOG.exists() else []
    except Exception:
        return []


_FEEDBACK_TAG_TIPS = {"偏咸": "主动再减盐", "偏油": "烹饪用油再收一收", "偏淡": "调味可稍补一点"}


def _feedback_tips(entries: list[dict], today: datetime | None = None) -> list[str]:
    """把最近一周的真实用餐反馈提炼成建议；没有反馈就一条不说。"""
    today = today or datetime.now()
    since = (today - timedelta(days=7)).date().isoformat()
    recent = [e for e in entries if str(e.get("ts", ""))[:10] >= since]
    tips: list[str] = []
    tag_counts = Counter(t for e in recent for t in (e.get("tags") or []))
    for tag, tip in _FEEDBACK_TAG_TIPS.items():
        if tag_counts.get(tag, 0) >= 2:
            tips.append(f"本周 {tag_counts[tag]} 餐反馈『{tag}』——{tip}。")
        if len(tips) >= 2:
            break
    rated = [e for e in recent if isinstance(e.get("rating"), (int, float))]
    if len(tips) < 2 and len(rated) >= 2:
        avg = sum(e["rating"] for e in rated) / len(rated)
        if avg <= 2:
            tips.append(f"本周 {len(rated)} 餐平均评分仅 {avg:.1f} 分——建议换换菜式或调整做法。")
    return tips


def _feedback_summary(entries: list[dict], today: datetime | None = None) -> dict:
    today = today or datetime.now()
    since = (today - timedelta(days=7)).date().isoformat()
    recent = [e for e in entries if str(e.get("ts", ""))[:10] >= since]
    tags = Counter(t for e in recent for t in (e.get("tags") or []))
    return {"count": len(recent), "tags": dict(tags)}


@router.post("/reports/feedback")
def save_feedback(payload: dict):
    """记录一次用餐反馈：{dish, rating(1-5), tags?, comment?}。"""
    dish = str(payload.get("dish") or "").strip()[:40]
    rating = payload.get("rating")
    if not dish:
        raise HTTPException(status_code=400, detail="dish 不能为空")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="rating 必须是 1-5 的整数")
    tags = [str(t).strip()[:8] for t in (payload.get("tags") or []) if str(t).strip()][:4]
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "dish": dish,
        "rating": rating,
        "tags": tags,
        "comment": str(payload.get("comment") or "").strip()[:120],
    }
    data = _read_feedback_log()
    data.append(entry)
    try:
        atomic_write_json(_FEEDBACK_LOG, data, indent=None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"反馈写入失败：{exc}")
    return {"saved": True, "count": len(data)}


def _pantry_restock_tips(log: list[dict], owned: set[str], today: datetime | None = None) -> list[str]:
    """Suggest restocking frequently repurchased items that are currently missing."""
    today = today or datetime.now()
    since = (today - timedelta(days=14)).date().isoformat()
    counts = Counter(e["item"] for e in log if e.get("type") == "purchase" and e.get("date", "") >= since)
    tips: list[str] = []
    for item, count in counts.most_common():
        if len(tips) >= 2:
            break
        if item in owned or count < 2:
            continue
        tips.append(f"「{item}」近两周已补货 {count} 次、冰箱暂时没有——下次采购记得带上。")
    return tips


def _recent_meals() -> list[dict]:
    """近 7 天记账记录（weekly 与 summary 共用）。窗口与 weekly_report 同源。"""
    data = read_meals()
    since, _now = week_window()
    return [m for m in data if datetime.fromisoformat(m["ts"]) >= since]


def _active_conditions() -> list[str]:
    """活跃成员的健康条件标签（读不到就空表，不阻塞小结）。"""
    try:
        from api.routes.preferences_route import _read_family
        family = _read_family()
        if not family:
            return []
        active = next(
            (m for m in family["members"] if m["id"] == family.get("active_id")), None
        )
        return list((active or {}).get("profile", {}).get("conditions") or [])
    except Exception:
        return []


def _weekly_evidence() -> dict | None:
    """汇总本周证据包（供指纹与 LLM prompt）；无数据返回 None。"""
    recent = _recent_meals()
    if not recent:
        return None
    dishes = Counter(m["dish"] for m in recent if m.get("dish"))
    lights = Counter(l for m in recent for l in m.get("lights", []))
    trends = _light_trends(recent, week_window()[0])
    tags = Counter(
        t for e in _read_feedback_log()
        for t in (e.get("tags") or [])
        if str(e.get("ts", ""))[:10] >= (datetime.now() - timedelta(days=7)).date().isoformat()
    )
    try:
        from storage.feedback import recent_down_dishes
        downs = recent_down_dishes(days=7, limit=3)
    except Exception:
        downs = []
    return {
        "meals": len(recent),
        "top_dishes": dishes.most_common(5),
        "lights": dict(lights),
        "trends": trends,
        "feedback_tags": dict(tags),
        "down_dishes": downs,
        "conditions": _active_conditions(),
    }


_SUMMARY_PROMPT = (
    "你是饮食健康小结助手。根据以下本周真实数据写 3-5 句中文小结，第二人称『你』。"
    "要求：具体点名菜名与红绿灯/趋势；有数据才下结论，缺数据就说样本还少；"
    "严禁编造数据之外的事实；语气自然不堆砌；直接输出小结正文。\n\n本周数据：\n{evidence}"
)


@router.get("/reports/weekly-summary")
def weekly_summary(refresh: bool = False):
    """本周 AI 自然语言小结：数据指纹缓存，数据不变不重复调用 LLM。

    refresh=1 跳过缓存强制重生成（用户点『重新生成』）。"""
    evidence = _weekly_evidence()
    if evidence is None:
        return {"ai_summary": None, "reason": "no_data"}
    fingerprint = hashlib.md5(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if not refresh:
        try:
            cache = json.loads(_SUMMARY_CACHE.read_text(encoding="utf-8"))
            if cache.get("fingerprint") == fingerprint and cache.get("ai_summary"):
                return {"ai_summary": cache["ai_summary"], "cached": True}
        except Exception:
            pass
    try:
        from infrastructure.model_name import extract_message_text, get_summary_llm
        llm = get_summary_llm(temperature=0.3, max_tokens=500)
        lines = [
            f"餐数：{evidence['meals']}",
            f"常吃：{('、'.join(d for d, _ in evidence['top_dishes'])) or '无'}",
            f"红绿灯计数：{evidence['lights']}",
            f"趋势：{evidence['trends']}",
            f"用餐反馈标签：{evidence['feedback_tags'] or '无'}",
            f"近期被踩菜品：{('、'.join(evidence['down_dishes'])) or '无'}",
            f"健康条件标签：{('、'.join(evidence['conditions'])) or '无'}",
        ]
        response = llm.invoke(_SUMMARY_PROMPT.format(evidence=chr(10).join(lines)))
        summary = extract_message_text(response, allow_reasoning_fallback=True)
    except Exception as exc:
        return {"ai_summary": None, "reason": f"llm_failed: {exc}"[:160]}
    if not summary:
        return {"ai_summary": None, "reason": "empty"}
    try:
        atomic_write_json(
            _SUMMARY_CACHE,
            {"fingerprint": fingerprint, "ai_summary": summary,
             "ts": datetime.now().isoformat(timespec="seconds")},
            indent=1,
        )
    except Exception:
        pass
    return {"ai_summary": summary, "cached": False}















