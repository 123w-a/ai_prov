# -*- coding: utf-8 -*-
"""小膳管家 · 5 场景冒烟脚本（改完必跑）。

设计目标只有一个：**把「总以为可以了」变成一道跑得出来的门槛。**

为什么是这 5 个场景：它们对应产品最核心的 5 种"轮次形态"，
也是最容易被某次改动悄悄打断的地方——
一个词表加错，泛推荐就出卡片了；一个正则收紧，画像加入就被误拦了。

    1. 泛推荐     → 出编号候选清单（不是卡片、不是直通闲聊）
    2. 序号选定   → 认出序号，进单菜确认轮
    3. 过敏拦截   → 真下料必须拦住（安全不可退让）
    4. 追问门控   → 该问才问，问过不重复问
    5. 新成员加入 → 画像加入**不得**被当成下料拦截（线上真实翻车点）

跑法（项目根目录）：

    .\\.venv\\Scripts\\python.exe scripts\\smoke.py

约 3~5 秒出结果；全绿退出码 0，任一红退出码 1（可直接挂到提交前钩子）。

覆盖范围（诚实声明）
-------------------
本脚本只测**确定性判定层**，不调用任何 LLM、不联网、不读真实档案：
    ✅ 意图分类 / 序号解析 / 过敏原硬判定 / 门控与路由 / 护栏路由
    ❌ LLM 生成质量、卡片渲染、图片链路、RAG 召回
后四项需要真跑一轮对话，属于 e2e（见 .tmpdir/e2e/），不在这里假装覆盖。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402

from agent.graph import (  # noqa: E402
    parse_candidate_index,
    profile_gate_node,
    profile_gate_route,
    verify_route,
)
from agent.turn_decision import classify_turn_intent  # noqa: E402
from domain.allergen_rules import (  # noqa: E402
    audit_allergens,
    requested_allergen_additions,
)
from domain.constraint_rules import build_matrix  # noqa: E402

PEANUT = ["花生"]
PEANUT_LABEL = "花生及其制品"


class Scenario:
    """一个场景 = 一组断言。任一条红，整个场景红。"""

    def __init__(self, name: str, why: str) -> None:
        self.name = name
        self.why = why
        self.checks: list[tuple[str, Any, Any]] = []

    def expect(self, label: str, actual: Any, wanted: Any) -> "Scenario":
        self.checks.append((label, actual, wanted))
        return self

    @property
    def failed(self) -> list[tuple[str, Any, Any]]:
        return [(l, a, w) for l, a, w in self.checks if a != w]

    @property
    def ok(self) -> bool:
        return not self.failed


def _gate(text: str, active: list[str] | None = None) -> dict:
    """在"档案无声明"的干净环境下跑一次门控节点。"""
    state = {
        "messages": [HumanMessage(content=text)],
        "profile_ready": True,
        "profile_missing": [],
    }
    with patch("agent.graph._active_profile_conditions", return_value=list(active or [])):
        return profile_gate_node(state)


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []

    # ── 1. 泛推荐：必须是"推荐"意图，且不应先被追问拦下 ──────────────
    s = Scenario("泛推荐", "泛推荐要出编号候选清单，不能变成卡片/闲聊/追问")
    s.expect(
        '「推荐几道清淡的家常菜」→ recommend',
        classify_turn_intent("推荐几道清淡的家常菜"),
        "recommend",
    )
    s.expect(
        '「晚上想吃什么好」→ recommend',
        classify_turn_intent("晚上想吃什么好"),
        "recommend",
    )
    s.expect(
        "泛推荐轮不被门控拦下（profile_ready=True）",
        _gate("推荐几道清淡的家常菜")["profile_ready"],
        True,
    )
    scenarios.append(s)

    # ── 2. 序号选定：序号要认得出来，且要进单菜确认轮 ────────────────
    s = Scenario("序号选定", "选定轮认不出序号 → 模型自己猜；认成推荐 → 又出一批候选")
    s.expect('「就第2个吧」→ 2', parse_candidate_index("就第2个吧"), 2)
    s.expect('「第二个」→ 2', parse_candidate_index("第二个"), 2)
    s.expect('「第2道」→ 2', parse_candidate_index("第2道"), 2)
    s.expect('「选2吧，少盐点」→ 2', parse_candidate_index("选2吧，少盐点"), 2)
    s.expect("无序号的「随便」→ None", parse_candidate_index("随便吧"), None)
    s.expect(
        "有候选历史 + 序号 → confirm_one",
        classify_turn_intent("选2吧", has_prior_candidates=True, candidate_index=2),
        "confirm_one",
    )
    scenarios.append(s)

    # ── 3. 过敏拦截：真下料，一个都不能漏 ─────────────────────────────
    s = Scenario("过敏拦截", "安全护栏只允许「多拦」，不允许漏拦")
    for text in (
        "给我加点花生",
        "把花生加进去",
        "这道菜加入花生酱",
        "帮我放点花生碎",
        "我加入花生",
    ):
        s.expect(
            f"「{text}」→ 命中花生",
            requested_allergen_additions(text, PEANUT),
            [PEANUT_LABEL],
        )
    s.expect(
        "归一过敏原同样要原文命中（「我要加芒果」）",
        requested_allergen_additions("我要加芒果", ["芒果"]),
        ["芒果及其制品"],
    )
    s.expect(
        "芒果别名进入可选层后必须硬拦",
        bool(audit_allergens("杨枝甘露", ["mango"], use_optional=True)),
        True,
    )
    unresolved_rows = build_matrix(
        [{"name": "酒酿甜品", "ingredients": "酒精"}],
        [{"name": "奶奶", "profile": {"allergens": ["酒精"]}}],
    )
    s.expect(
        "未归一过敏原不得判可吃",
        unresolved_rows[0]["verdict"],
        "待确认",
    )
    s.expect(
        "命中后路由必须走 blocked",
        verify_route({"verify_status": "blocked"}),
        "blocked",
    )
    s.expect(
        "否定句不得误拦（「不加花生」）",
        requested_allergen_additions("不加花生", PEANUT),
        [],
    )
    scenarios.append(s)

    # ── 4. 追问门控：该问才问，问过不重复问 ───────────────────────────
    s = Scenario("追问门控", "信息不足要追问；已自述过就绝不能再追问一次")
    s.expect(
        '「我想吃降血糖的菜」→ 门控拦下（ready=False）',
        _gate("我想吃降血糖的菜")["profile_ready"],
        False,
    )
    s.expect(
        "门控拦下后路由 → ask",
        profile_gate_route({"profile_ready": False}),
        "ask",
    )
    s.expect(
        "门控放行后路由 → ready",
        profile_gate_route({"profile_ready": True}),
        "ready",
    )
    s.expect(
        "已自述「我有高血压」→ 不再追问",
        _gate("我有高血压")["profile_ready"],
        True,
    )
    s.expect(
        "档案已声明 → 不再追问",
        _gate("今晚吃什么？", active=["高血压"])["profile_ready"],
        True,
    )
    scenarios.append(s)

    # ── 5. 新成员加入：画像加入不得被当成下料（P0 线上翻车点） ────────
    s = Scenario("新成员加入", "「把她加入画像：对花生过敏」曾被误判成「把花生加入菜」→ 无理由硬拦截")
    for text in (
        "妹妹还没有加入家庭画像，请把她加入：对花生过敏，口味清淡",
        "小雨还没有加入家庭画像，请把她加入：对花生过敏",
        "请把妹妹加入：对花生过敏，口味清淡",
        "添加家庭成员妹妹，她对花生过敏",
        "给妹妹建档，花生过敏，口味清淡",
    ):
        s.expect(f"画像加入不得拦截：{text[:14]}…", requested_allergen_additions(text, PEANUT), [])
    # 对照组：同一句话里真的下料，仍必须拦
    s.expect(
        "对照：同一句式真下料仍拦（「我加入花生」）",
        requested_allergen_additions("我加入花生", PEANUT),
        [PEANUT_LABEL],
    )
    s.expect(
        "对照：跨成员也要拦（「添加花生到这个家庭的菜单」）",
        requested_allergen_additions("添加花生到这个家庭的菜单", PEANUT),
        [PEANUT_LABEL],
    )
    scenarios.append(s)

    return scenarios


def main() -> int:
    started = time.monotonic()
    scenarios = build_scenarios()

    print()
    print("=" * 74)
    print("  小膳管家 · 冒烟测试（5 场景 · 确定性判定层 · 不烧 LLM）")
    print("=" * 74)

    total = passed = 0
    for index, scenario in enumerate(scenarios, start=1):
        total += 1
        mark = "PASS" if scenario.ok else "FAIL"
        if scenario.ok:
            passed += 1
        print(f"\n[{mark}] 场景 {index}/5 · {scenario.name}")
        print(f"        为什么要测：{scenario.why}")
        for label, actual, wanted in scenario.checks:
            hit = actual == wanted
            flag = "  ok " if hit else "  ✗  "
            print(f"{flag}{label}")
            if not hit:
                print(f"        实得: {actual!r}")
                print(f"        应为: {wanted!r}")

    elapsed = time.monotonic() - started
    print()
    print("-" * 74)
    if passed == total:
        print(f"结果：{passed}/{total} 场景通过 · 耗时 {elapsed:.1f}s")
        print("确定性判定层无回归。注意：LLM 生成 / 卡片 / 配图 / RAG 未覆盖。")
    else:
        print(f"结果：{passed}/{total} 场景通过（{total - passed} 个红）· 耗时 {elapsed:.1f}s")
        for index, scenario in enumerate(scenarios, start=1):
            if not scenario.ok:
                print(f"  红：场景 {index} · {scenario.name}")
        print("先修红的，别接着改别的。")
    print("=" * 74)
    print()
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
