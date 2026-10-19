"""体重记录 API（2026-10-18 新增）：由用户自己录入体重，供档案页算 BMI 与体重变化。

【为什么单开一口，而不是并进 /profile】

`PUT /api/profile/members/{id}` 是**整成员覆盖**语义 —— 体重历史塞进成员对象，
用户下一次点「编辑」保存时整段历史就被抹掉。所以它是独立的只增记录，
落 `data/weight_log.json`。

【为什么 POST 用名字而不是 member id】

与 `meals.json` 的归属链一致（`member`/`members` 存的都是名字）。本仓已有过一次
教训：同一实体两套标识会让回填与统计大面积匹配不上。

【为什么 GET 默认返回全家】

档案页要一次画完所有成员卡片。按人拉 N 次会变成 N 次读盘，且各次之间的
`weight_log.json` 可能已被写入，同一屏上出现两个时点的数据。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.routes.preferences_route import _read_family
from storage.weight_log import all_weight_summaries, append_weight, member_weight_summary

router = APIRouter()


class WeightEntryPayload(BaseModel):
    """记一条体重。`date` 省略 = 今天（本地日期）。"""

    name: str = Field(min_length=1)
    weight_kg: float
    date: str | None = None


@router.get("/weight/log")
def get_weight_log(name: str = ""):
    """体重记录小结。`name` 省略 → 全家每位成员各一份。

    每份形如::

        {"member": "我", "latest": {"date": "2026-10-18", "weight_kg": 63.0},
         "earliest": {...}, "change_kg": null, "count": 1, "entries": [...]}

    `change_kg` 在**只有一条记录**时是 `null`（不是 0.0）：一条记录算不出"变化"，
    写 0 会被读成"体重没变"。前端据此显示「记录不足两次」而不是「+0.0kg」。

    成员名单取自 `profile.json`（家里有谁）：一位从没记过体重的成员仍然要有
    小结（`count: 0`），从列表里消失会被读成"这个人不在这个家里"——与
    `/reports/member-weekly` 的成员卡片同一条纪律。
    """
    family = _read_family()
    members = [m for m in (family or {}).get("members") or [] if isinstance(m, dict)]
    names = [str(m.get("name") or "").strip() for m in members]
    names = [n for n in names if n]

    who = str(name or "").strip()
    if who:
        # 指定名字时不校验他是否在档案里：周报与诊断口也按名字查，
        # 而档案刚被改动的瞬间两者可能不同步。查不到就是空小结，不是 404。
        return {"by_member": {who: member_weight_summary(who)}}
    return {"by_member": all_weight_summaries(names)}


@router.post("/weight/log")
def post_weight_log(payload: WeightEntryPayload):
    """记一条体重。**同一天重复记 → 覆盖当天那条**（一天量两次不是两个数据点）。

    体重超出合理区间或日期格式坏 → 400，**不写坏数据、不夹紧到边界值**
    （夹紧会把一个明显的手滑静默变成一条看起来正常的记录）。
    """
    try:
        entry = append_weight(payload.name, payload.weight_kg, payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "entry": entry,
        "summary": member_weight_summary(payload.name),
    }
