# agent_graph.py：组装 LangGraph 循环 Agent（模型/断点/节点/状态图）
# 只负责"编排"：把提示词、工具、LLM、记忆、路由串成一张可运行的图
# 具体工具怎么干活看 agent_tools.py，提示词内容看 agent_prompts.py

import json#结构化回答打包成 JSON 字符串落进消息
import re
from pathlib import Path#读取 data/profile.json（家庭档案激活成员）
import openai#捕获上游 LLM 偶发 5xx/超时异常做重试
import threading#failover 并发锁
import time#重试间隔用
import sqlite3#持久化短期记忆（断点续跑、循环状态保存）
from model_name import (
    extract_message_text,
    get_langchain_llm,
    get_summary_llm,
    resolve_provider,
)
from langchain_core.messages import (  # 系统提示词节点用 + 长对话压缩用
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    RemoveMessage,#删除消息
)
# LangGraph 核心替换导入：用 StateGraph 手动搭流程图，替代 create_agent 的线性执行
from langgraph.graph import StateGraph, END, MessagesState#状态图+结束标志+状态
from langgraph.prebuilt import ToolNode, tools_condition  # 内置工具节点 + 是否继续调用工具的路由判断
from langgraph.checkpoint.sqlite import SqliteSaver#持久化短期记忆（断点续跑、循环状态保存）

from agent_trace import trace_node
from agent_prompts import SYSTEM_PROMPT#最上层的提示词从这里输出ai的最先回复
from agent_tools import find_recipe_image, set_query_transform_llm, tools, web_search
from agent_chains import build_structured_answer, rank_recipes#LCEL 结构化链(prompt|llm|parser)+排序+格式自动重试
from agent_schemas import DishMatrixItem, GuardrailItem  # 结构化输出的确定性注入字段
#build_structured_answer标准链+parser检查出错误后再进行重试
from nutrition_rules import detect_conditions, audit, describe, RULES, conditions_from_profile  # L3 硬护栏：确定性健康禁忌审计
from allergen_rules import (  # 过敏原 L3 硬护栏：与慢病规则分开，避免改变既有慢病降级语义
    allergen_label,
    audit_allergen_advisories,
    audit_allergens,
    describe_allergens,
    suggest_safe_dishes,
)
from constraint_rules import build_matrix, build_member_adjustments


# 画像自主采集默认关闭：关闭时候选提取、确认提示和写入链路均不生效。
PROFILE_MEMORY_ENABLED = False


def _active_profile_conditions() -> list:
    """读 data/profile.json 激活成员的 conditions，映射成硬护栏规则键。

    P1 多画像与 L3 护栏的接线：档案里写了痛风/孕期，即使消息只说「想吃火锅」，
    硬护栏也要同口径启用。读不到档案/解析失败一律返回空（护栏兜底不因档案缺失而崩）。
    """
    try:
        path = Path(__file__).resolve().parent / "data" / "profile.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next((m for m in members if m.get("id") == active_id), members[0])
            profile = member.get("profile") or {}
        else:  # v1 平铺档案
            profile = raw
        return conditions_from_profile(profile)
    except Exception:
        return []


def _active_profile_allergens() -> list:
    """读取激活成员的过敏原自由文本；档案缺失或损坏时返回空列表。"""
    try:
        path = Path(__file__).resolve().parent / "data" / "profile.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            active_id = raw.get("active_id")
            member = next((m for m in members if m.get("id") == active_id), members[0])
            profile = member.get("profile") or {}
        else:  # v1 平铺档案
            profile = raw
        values = profile.get("allergens")
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item or "").strip()]
    except Exception:
        return []


def _family_allergens() -> list:
    """读取全家成员过敏原并集；只用于过敏原护栏，不改变慢病条件口径。"""
    try:
        path = Path(__file__).resolve().parent / "data" / "profile.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        profiles = []
        if isinstance(members, list) and members:
            profiles = [
                member.get("profile") or {}
                for member in members
                if isinstance(member, dict)
            ]
        else:  # v1 平铺档案
            profiles = [raw]
        merged = []
        for profile in profiles:
            values = profile.get("allergens") if isinstance(profile, dict) else None
            if not isinstance(values, list):
                continue
            for item in values:
                text = str(item or "").strip()
                if text and text not in merged:
                    merged.append(text)
        return merged
    except Exception:
        return []


def _allergens_for_audit() -> list:
    """主菜生成只审计激活成员；其他成员由逐菜分餐矩阵单独处理。"""
    return _active_profile_allergens()


def _profile_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "profile.json"


def _profile_health() -> tuple[dict, str]:
    """返回 (档案字典, 错误说明)。

    错误说明非空 = 档案存在但不可用。这时护栏会降级，**必须在右栏如实告知**：
    静默当成"没有约束"等于让护栏在用户不知情的情况下失效。
    """
    path = _profile_path()
    if not path.exists():
        return {}, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"档案文件解析失败（{type(exc).__name__}）"
    if not isinstance(raw, dict):
        return {}, "档案文件结构不是对象"
    if not raw:
        return {}, "档案文件内容为空"
    members = raw.get("members")
    if isinstance(members, list) and members:
        usable = [
            m for m in members
            if isinstance(m, dict) and isinstance(m.get("profile"), dict)
        ]
        if not usable:
            return {}, "档案里没有任何可用的成员画像"
    return raw, ""


def _load_profile() -> dict:
    """读取 data/profile.json 原始字典；缺失/损坏返回 {}（不抛异常、不阻断主流程）。"""
    return _profile_health()[0]


def _active_member_name() -> str:
    """当前激活成员的名字；取不到时返回空串，前端据此不显示「主菜面向谁」。"""
    raw = _load_profile()
    members = raw.get("members")
    if isinstance(members, list) and members:
        active_id = raw.get("active_id")
        member = next(
            (m for m in members if isinstance(m, dict) and m.get("id") == active_id),
            None,
        )
        if member is None:
            member = next((m for m in members if isinstance(m, dict)), {})
        return str(member.get("name") or "").strip()
    return str(raw.get("name") or "").strip()


def _family_members() -> list[dict]:
    """读取同餐成员及画像；只读、容错，档案缺失时不阻断主流程。"""
    try:
        path = Path(__file__).resolve().parent / "data" / "profile.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        members = raw.get("members")
        if isinstance(members, list) and members:
            result = []
            for member in members:
                if not isinstance(member, dict):
                    continue
                name = str(member.get("name") or "").strip()
                profile = member.get("profile")
                if not name or not isinstance(profile, dict):
                    continue
                result.append({"name": name, "profile": profile})
            return result
        # v1 平铺档案兼容：整份画像视为一位成员。
        return [{"name": "我", "profile": raw}]
    except Exception:
        return []


def _apply_family_differentiation(answer, members=None):
    """用确定性规则覆盖模型自填矩阵，并生成稳定的一人一句调整。"""
    family_members = _family_members() if members is None else members
    try:
        rows = build_matrix(
            [recipe.model_dump() for recipe in answer.recipes],
            family_members or [],
        )
        answer.dish_matrix = [DishMatrixItem(**row) for row in rows]
        answer.member_adjustments = build_member_adjustments(rows)
    except Exception:
        # 矩阵只是差异化展示层，任何异常都不能影响已经通过审计的主卡片。
        answer.dish_matrix = []
        answer.member_adjustments = []
    return answer


def _merged_conditions(user_text: str) -> list:
    """消息推断 + 档案声明 的护栏规则键合并（保持顺序，去重）。"""
    merged = detect_conditions(user_text)
    for cond in _active_profile_conditions():
        if cond not in merged:
            merged.append(cond)
    return merged

# 稳定输出规则：覆盖旧提示词中的多菜分支，保证流式正文和结构化卡片一致。
SINGLE_RECIPE_RULE = (
    "\n\n【默认单菜规则】用户没有明确要求多个选择时，"
    "每次最终只回答最合适的一道菜，不要列出第二道、备选菜或并列方案。"
)
# 两阶段点菜第一阶段规则：泛推荐只出编号候选清单，不出做法、不出卡片。
# 编号是「用户说第 N 道」的唯一锚点，格式必须固定，后端靠编号解析做确定性映射。
CANDIDATE_LIST_RULE_TEMPLATE = (
    "\n\n【候选清单规则·本轮最高优先级】"
    "本轮用户没有点名具体哪一道菜，属于「泛推荐」。"
    "因此本轮只输出候选菜名清单：不要写做法、步骤、调料、火候，不要输出 JSON，不要生成卡片，不要配图。"
    "格式必须严格是（每行一条，编号从 1 开始，菜名后接「 —— 」再写一句 20 字以内的推荐理由）：\n"
    "1. 菜名 —— 理由\n"
    "2. 菜名 —— 理由\n"
    "按用户提到的食材和健康约束给出 {count} 道；每道都必须是完整菜名（如「番茄炒蛋」），不能只写单个食材。"
    "最后另起一行写「回复序号就行，我再把这一道的完整做法给你。」，不要用问句结尾。"
)


def _candidate_list_rule(messages) -> str:
    """用户明说「多来几道」时给更长的候选清单，默认给 3 道。"""
    count = 5 if _wants_multiple_recipes(messages) else 3
    return CANDIDATE_LIST_RULE_TEMPLATE.format(count=count)

# --------------------------------------------------------------------------- #
#  1. 模型 & 工具绑定
# --------------------------------------------------------------------------- #
# 主脑运行模型：不传参即走"自适配"——优先读 .env 的 CHEF_PROVIDER 开关（想用哪个写哪个），
# 没配 / 配了但没 key → 自动用 configs 第一个可用的（已将 gpt 放第一，不写即默认 gpt）。
# 本文件不硬编码任何模型名，切换模型只改 .env，无需动代码。
provider = resolve_provider()#不写默认是.env中设置的第一个key
MAIN_AGENT_MAX_TOKENS = 4096  # DeepSeek 的推理 token 与正文共用上限，1024 会把长回答截成半句
llm = None
llm_with_tools = None
retrieval_llm = None
summary_llm = None
_FAILOVER_LOCK = threading.Lock()


def rebuild_llms(force_provider=None):
    """构建/重建全部模块级 LLM。failover 时换 provider 重跑整轮。"""
    global provider, llm, llm_with_tools, retrieval_llm, summary_llm
    provider = force_provider or resolve_provider()#不写默认是.env中设置的第一个key
    llm = get_langchain_llm(provider, max_tokens=MAIN_AGENT_MAX_TOKENS)#获取模型对象
    # 检索侧思考（查询改写 / 多查询 / HyDE）：高频低难度任务，跟随主 provider 保证可用性
    try:
        retrieval_llm = get_langchain_llm(provider, temperature=0.3, max_tokens=200)
    except Exception:
        retrieval_llm = llm
    # 历史摘要专用模型：DeepSeek 默认独立使用 deepseek-chat，避免 thinking 模型
    # 只把正文写进 reasoning_content；failover 到其他 provider 时不覆盖型号名。
    try:
        summary_llm = get_summary_llm(provider, temperature=0.3)
    except Exception:
        summary_llm = llm
    llm_with_tools = llm.bind_tools(tools)#传个大模型告诉他有什么工具和怎么正确的用变成json格式给LLM


rebuild_llms()#import 时构建一次


def failover_llms():
    """当前主 provider 黑洞后切换到备用 provider；返回实际切换到的名字或 None。"""
    from model_name import _provider_in_cooldown, mark_provider_down, pick_fallback_provider
    with _FAILOVER_LOCK:#并发请求同时触发 failover 时只换一次
        if _provider_in_cooldown(provider):
            return None  # 当前 provider 已在冷却，说明别处刚切过/试过
        alt = pick_fallback_provider(exclude=provider)
        if not alt:
            return None
        failed = provider
        rebuild_llms(alt)
        mark_provider_down(failed)#锁内标记，杜绝并发请求在标记前又切回旧家
    return alt

def _query_transform_adapter(system: str, user: str) -> str:
    try:
        return retrieval_llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        ).content
    except Exception:
        return ""
set_query_transform_llm(_query_transform_adapter, mode="multi")

# --------------------------------------------------------------------------- #
#  2. 断点持久化（SQLite checkpointer，thread_id 对应 checkpoint.db 里的单条任务断点）
# --------------------------------------------------------------------------- #
connection = sqlite3.connect(
    database="resources/checkpoint.db",
    check_same_thread=False
)

checkpointer = SqliteSaver(connection)
checkpointer.setup()

# --------------------------------------------------------------------------- #
#  3. 长对话压缩节点（替代原 SummarizationMiddleware）：LLM 推理前触发
#  历史消息超过阈值时，把更老的"用户/AI 对话"总结成要点、删掉原文，防上下文溢出
#  只总结 user/AI，跳过 ToolMessage 工具返回；总结 Prompt 针对膳食管家场景定制
# --------------------------------------------------------------------------- #
MAX_HISTORY_KEEP = 6  # 保留最近约 3 轮(user+ai)，更早的参与总结（调大以减少压缩触发、保住菜品编号上下文）
MAX_TOOL_CALLS_PER_TURN = 4  # 本轮最多执行 4 次工具，防止模型在刁钻输入下失控循环
# 「已经选定一道菜，只是顺带问一句健康问题」这类轮次收紧到 2 次工具：
# 实测「2吧…我的父亲高血压，可以吃这个吗」会让模型连搜 3 次网页把 4 次预算搜爆，
# 白烧 60s 还拿不到卡片；这类轮次营养依据本来就在知识库里，不需要反复联网。
# 取 2 而不是 1，是为了兼容模型一次批量调 2 个工具（否则会一个工具都跑不了）。
PICK_TURN_TOOL_BUDGET = 2
#MessagesState是所有的状态消息，包含 messages 属性
@trace_node("condense_history")
def maybe_condense(state: MessagesState):#压缩历史对话
    msgs = state["messages"]
    reset_turn_state = {
        "verify_attempts": 0,
        "verify_warning": "",
        "verify_status": "ok",
        "verify_violated": [],
        "tool_calls_in_turn": 0,
        "tool_budget_exhausted": False,
        "tool_budget": _turn_tool_budget(msgs),
    }
    # 最后一条消息不是用户发的或者没有消息的话就不执行后续操作
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {}#不改变状态，防止打断正在运行的时候
    # 只统计 user + ai 对话，跳开 ToolMessage 工具返回，避免把冗长搜索结果搅进摘要
    talk = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]#自动过滤没用消息
    if len(talk) <= MAX_HISTORY_KEEP:
        return reset_turn_state  # 还没到阈值，只重置本轮运行状态
    old = talk[:-MAX_HISTORY_KEEP]#从开始到倒数第max_history_keep，老的对话要总结后删除
    if not old:
        return reset_turn_state
    convo = "\n".join(#将列表转为字符串供llm生成摘要
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}：{m.content}"#用户：内容，ai：内容
        for m in old#循环把每一条旧对话转为字符串并拼接在一起
    )
    summary_prompt = (#提示词
        "请把这段小膳管家对话历史压缩成极简要点，严格按以下规则：\n"
        "①用户拥有的食材；②口味/忌口偏好（甜/辣/酸/控糖等）；\n"
        "③已推荐或已做的菜谱——【必须按用户原始提问顺序，逐道列出 第1道=… 第2道=… 第3道=… "
        "（有多道务必保留编号），并标注关键改动（如换甜口/换清淡）；不得合并、重命名、捏造菜名，"
        "用户没明确说过的菜标'无'】；\n"
        "④未完成的待办。用中文、分点、不超200字：\n\n" + convo
    )
    # LLM 偶发 502，简单重试，失败就先不压缩（不阻断主流程）
    summary = None#标记摘要是否成功生成
    for attempt in range(3):
        try:
            summary = extract_message_text(
                summary_llm.invoke([HumanMessage(content=summary_prompt)]),
                allow_reasoning_fallback=True,
            )
            break#服务器错误，网络错误，调用次数超过限制
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError):
            time.sleep(1 + attempt)
    if not summary:
        return reset_turn_state#摘要生成失败就跳过压缩，但仍重置本轮运行状态
    # 关键：被删 AIMessage 若带 tool_calls，它对应的 ToolMessage 必须"连坐"删除，
    # 否则孤儿 ToolMessage 留在历史里，下次请求 LLM 直接 400：
    # "No tool call found for function call output with call_id ..."
    doomed_call_ids = set()#删了旧消息也不会自动把工具调用的消息也删除所以要标记一起删
    for m in old:#循环旧消息
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:#获取工具调用信息，有就返回,m.tool_calls
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    doomed_call_ids.add(tc_id)#有的话就全部传入同一集合
    # 删除被总结掉的旧消息、插入摘要（RemoveMessage 真正从 state 移除，避免无限增长）
    removals = [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None)]#state里的消息看到RemoveMessage就删除
    if doomed_call_ids:
        removals += [
            RemoveMessage(id=m.id)
            for m in msgs
            if isinstance(m, ToolMessage)
            and getattr(m, "tool_call_id", None) in doomed_call_ids
            and getattr(m, "id", None)
        ]
    summary_msg = HumanMessage(content=f"[历史对话摘要，供参考]\n{summary}")#在state里看到HumanMessage就插入
    return {**reset_turn_state, "messages": removals + [summary_msg]}#返回给langgraph处理，看成是先删后加
#返回的是对状态的增量
# --------------------------------------------------------------------------- #
#  4. 节点1：LLM 思考节点（绑定系统提示词 + 工具调用能力）
# --------------------------------------------------------------------------- #
def _drop_orphan_tool_messages(messages):#过滤AIMessage tool_calls
    """过滤孤儿 ToolMessage：tool_call_id 在历史里找不到对应的 AIMessage tool_calls。
    存量 checkpoint 可能已有这种孤儿（旧压缩逻辑遗留），带着它请求 LLM 会直接 400：
    "No tool call found for function call output with call_id ..."
    只影响发给 LLM 的 payload，不改 state。"""
    valid_ids = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    valid_ids.add(tc_id)
    return [
        m for m in messages
        if not (isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) not in valid_ids)
    ]


def _latest_user_has_image(messages):
    """判断最近一条用户消息是否包含图片。"""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要") and not str(m.content).startswith("[健康护栏审核"):
            return _message_has_image(m)
    return False


def _message_text(message):
    """提取一条用户消息中的文字部分。"""
    if isinstance(message.content, list):
        return " ".join(
            part.get("text", "")
            for part in message.content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(message.content)


def _message_has_image(message):
    """判断一条图文消息是否包含图片。"""
    return isinstance(message.content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url"
        for part in message.content
    )


# 模型偶尔不走「先说人话」的路子，直接把结构化 JSON 当成正文吐出来
# （可能带 ```json 围栏、也可能裸 JSON）。这段文本会被当作 opening 显示在对话框和卡片里。
# 这里不猜、不截断，只做一件事：如果拿到的确实是控制 JSON，就抽它自己的 opening 字段。
_CONTROL_JSON_KEYS = (
    "opening", "recipes", "answer_kind", "candidates",
    "guardrails", "health_lights", "primary_member",
)


def _strip_code_fence(text: str) -> str:
    """去掉最外层 ```json / ``` 围栏，只留里面的内容。"""
    body = text.strip()
    if not body.startswith("```"):
        return body
    body = body[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _clean_opening_text(raw) -> str:
    """把 chef_think 最后一轮的内容清洗成可展示的纯文本。

    正常情况模型给人话，原样返回。异常情况模型整段吐控制 JSON —— 那种文本
    **不能**原样当正文（用户会在气泡里看到一坨 JSON）。此时抽 JSON 里的 opening，
    抽不到就返回空串：宁可少一句开场白，也不把 JSON 露给用户。
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    looks_json = text.startswith("{") or text.startswith("```")
    if not looks_json:
        return text
    body = _strip_code_fence(text)
    if not body.startswith("{"):
        return text
    try:
        data = json.loads(body)
    except Exception:
        return text
    if not isinstance(data, dict):
        return text
    inner = data.get("opening")
    if isinstance(inner, str) and inner.strip():
        return inner.strip()
    # 是控制 JSON 但里面没有可用的 opening：不要退化成把整坨 JSON 当正文。
    if any(key in data for key in _CONTROL_JSON_KEYS):
        return ""
    return text


_INTERNAL_REQUEST_MARKERS = (
    "【配图开关：开启】",
    "【本轮需要配图】",
)


def _strip_internal_request_markers(text):
    """移除仅用于后端控制、不能回显给用户的内部标记。"""
    cleaned = str(text or "")
    for marker in _INTERNAL_REQUEST_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def _current_request_text(text):
    """从用户消息中取出本次需求，排除每轮自动注入的长期偏好和内部标记。"""
    markers = (
        "【以上为自动加载约束，以下是本次需求】",
        "【以上为偏好约束，以下是本次需求】",
    )
    for marker in markers:
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    return _strip_internal_request_markers(text)


def _is_recipe_selection_request(text: str) -> bool:
    """只把已经进入菜品选择的请求送入图片链路，健康泛问答不启动搜图。"""
    text = str(text or "").strip()
    if any(marker in text for marker in ("帮我做", "做道", "做个", "做一份", "来道", "来个", "菜品", "菜谱", "食谱")):
        return True
    if any(marker in text for marker in ("推荐", "想吃")):
        return any(char in text for char in ("鸡", "鱼", "肉", "蛋", "虾", "豆腐", "面", "饭", "菜", "汤", "粥", "粉"))
    return False


def _wants_recipe_images(messages):
    """判断本轮是否由后端明确授权配图。

    配图跟随「已选定的一道菜」：健康闲聊/追问/餐馆/上门服务/候选列表阶段都不烧图；
    点名一道菜或确认（含从候选中选第 N 道）时才进入配图链路，最终仍以 recipes 为准。"""
    raw_text = _latest_user_text(messages, strip_internal=False)
    text = _current_request_text(raw_text)
    if not text:
        return False
    intent = _classify_turn_intent(messages)
    is_specific = _is_specific_dish_request(text)
    # 泛推荐即使带内部配图开关，也必须先走候选清单。开关只能表达“已获准配图”，
    # 不能反过来把尚未选定的菜跳过候选阶段。
    if intent == "recommend" and not is_specific:
        return False
    if any(marker in raw_text for marker in _INTERNAL_REQUEST_MARKERS):
        return True
    return intent in ("confirm_one", "change_one") or (
        intent == "recommend" and is_specific
    )


def _recent_recipe_names(messages, limit=8):
    """从最近结构化卡片里取菜名，给确定/指代判断一个确定性锚点。"""
    names = []
    for m in reversed(messages or []):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        recipes = data.get("recipes") if isinstance(data, dict) else None
        if not isinstance(recipes, list):
            continue
        for recipe in recipes:
            if not isinstance(recipe, dict):
                continue
            name = str(recipe.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
                if len(names) >= limit:
                    return names
    return names


def _mentions_recent_recipe(text: str, names) -> bool:
    text = str(text or "")
    return any(name and name in text for name in names)


def _has_recipe_index_ref(text: str) -> bool:
    return bool(re.search(r"(第[一二三四五六七八九十123456789]\s*[道个款样号份]|一道|二道|三道|第一道|第二道|第三道|这个|这道|这道菜|来这个|做这个|吃这个)", str(text or "")))


# --------------------------------------------------------------------------- #
#  3.6 两阶段点菜交互：候选列表（第一跳）→ 用户选定（第二跳）
#  判定必须确定性：先看用户是否已点名一道具体的菜，只有「泛推荐」才出候选列表。
#  第一阶段只给编号候选（纯文本，不出卡片、不配图），第二阶段选定后才出单卡片+配图。
# --------------------------------------------------------------------------- #
# 纯食材字表：出现在「做/吃」后面且整段只有食材字时，说明用户给的是食材而不是一道菜
_PURE_INGREDIENT_CHARS = set(
    "鸡鸭鱼肉蛋虾蟹奶豆米面饭菜汤粥粉薯瓜茄葱姜蒜椒菇木耳叶花萝卜笋藕玉米山药枣花生"
    "芝麻紫菜海带粉丝年糕豆腐青菜白菜菠菜生菜油菜番茄西红柿土豆胡萝卜洋葱西兰花黄瓜"
    "南瓜冬瓜丝瓜韭菜芹菜豆角豌豆青豆燕麦藜麦小米糙米黑米意面挂面米粉河粉麦片排"
)
# 这些词作为完整菜名时说明用户没点名菜，而是要一个方向或一顿饭。
# 不能按“包含单字”判断，否则「徐福烩饭」「番茄鸡蛋面」这类完整菜名会被误杀。
_GENERIC_DISH_NAMES = (
    "什么", "点菜", "菜品", "菜谱", "食谱", "推荐", "几道", "几个", "一道", "两道", "三道",
    "吃的", "东西", "家常菜", "快手菜", "简单", "清淡", "健康", "营养", "减脂", "低卡",
    "晚饭", "午饭", "中饭", "早餐", "晚餐", "夜宵", "宵夜", "饭", "菜", "汤", "面", "餐",
)
# 出现在菜名中的泛推荐/口味描述，说明这是约束条件而非具体菜名。
_GENERIC_DISH_PHRASES = (
    "什么", "点菜", "菜品", "菜谱", "食谱", "推荐", "几道", "几个", "一道", "两道", "三道",
    "吃的", "东西", "家常菜", "快手菜", "简单", "清淡", "健康", "营养", "减脂", "低卡",
    "晚饭", "午饭", "中饭", "早餐", "晚餐", "夜宵", "宵夜",
)
_TASTE_ONLY_PATTERN = re.compile(
    r"^(?:更|稍微|偏|太|非常)?(?:辣|咸|甜|酸|苦|麻|重口|少盐|少油|低盐|低油)"
    r"(?:一点|一些|点|些|的)?$"
)
_NEGATED_EATING_PATTERN = re.compile(
    r"(?:吃不了|吃不下|吃不完|吃不惯|不能吃|不爱吃|不想吃|不吃|吃过了|吃着|吃腻)"
)
_TASTE_COMPLAINT_PATTERN = re.compile(
    r"(?:腥味|膻味|苦味|怪味|味道|口感|太咸|太油|太辣|太甜)"
)
_DISH_REQUEST_NEGATIVE_LOOKAHEAD = r"(?!不了|不下|不完|不惯|不能|不爱|不想|不)"
_DISH_REQUEST_PATTERN = re.compile(
    r"(?:想吃|要吃|想吃点|来个|来道|来一份|做道|做个|做一份|做一下|帮我做|给我做|就做"
    r"|推荐(?:一|两|三|几)?[道个份款]?|吃)" + _DISH_REQUEST_NEGATIVE_LOOKAHEAD +
    r"([^\s，。、！？；;：:（）()【】\[\]]{1,10})"
)
_LEADING_QUANTIFIER = re.compile(r"^(?:一|两|三|四|五|几|个|道|份|款|些|点)+")
# 菜名里混进疑问/意图残留（「火锅吗」「做法」）时，说明这轮不是点名一道菜
_DISH_NAME_NOISE = re.compile(r"(怎么|如何|做法|热量|多少|能不能|可以|适合|吗|呢|图|照片|图片|推荐|选择|几道)")


def _is_generic_dish_name(name: str) -> bool:
    """判断触发词后截出的是泛推荐/口味约束，而不是一道具体菜。"""
    return (
        name in _GENERIC_DISH_NAMES
        or any(word in name for word in _GENERIC_DISH_PHRASES)
        or bool(_TASTE_ONLY_PATTERN.fullmatch(name))
    )


def _is_specific_dish_request(text) -> bool:
    """用户是否已经点名了一道具体的菜。

    - 「我想吃番茄炒蛋」「推荐一道清蒸鲈鱼」→ True：直接出单卡片；
    - 「我有鸡胸肉和青菜」「推荐几道菜」「不知道吃什么」→ False：先出候选列表。
    只报食材、口味约束或“吃不了某味道”都不算点名。
    """
    raw = str(text or "").strip()
    if not raw:
        return False
    if _NEGATED_EATING_PATTERN.search(raw) and not _DISH_REQUEST_PATTERN.search(raw):
        return False
    for match in _DISH_REQUEST_PATTERN.finditer(raw):
        name = _LEADING_QUANTIFIER.sub("", match.group(1).strip())
        if len(name) < 2:
            continue
        if _is_generic_dish_name(name):
            continue
        if _TASTE_COMPLAINT_PATTERN.search(name):
            continue
        if all(char in _PURE_INGREDIENT_CHARS for char in name):
            continue
        if _DISH_NAME_NOISE.search(name):
            continue
        return True
    return False


def is_specific_dish_request(text) -> bool:
    """对外别名：路由层（chat_route）与 Agent 层必须同源，避免两层判定漂移。"""
    return _is_specific_dish_request(text)


# 明确要图时用户要的是成品图，不是候选清单：此时不进入候选阶段。
# 纯食材清单（「我有鸡胸肉和青菜」）也必须进候选阶段：用户给食材时直接塞一张卡片，
# 正是两阶段交互要消灭的现象。
_INGREDIENT_KEYWORDS = (
    "鸡胸肉", "鸡腿", "鸡翅", "鸡蛋", "鸭蛋", "猪肉", "牛肉", "羊肉", "五花肉", "排骨", "肉末",
    "虾", "鱼", "鱿鱼", "蛤蜊", "蟹", "豆腐", "豆干", "腐竹",
    "青菜", "白菜", "菠菜", "生菜", "油菜", "西兰花", "花菜", "黄瓜", "南瓜", "冬瓜", "丝瓜",
    "茄子", "土豆", "胡萝卜", "萝卜", "洋葱", "青椒", "番茄", "西红柿", "蘑菇", "香菇", "木耳",
    "豆角", "豌豆", "玉米", "山药", "莲藕", "韭菜", "芹菜", "粉丝", "年糕", "面条", "挂面",
    "米粉", "米饭", "大米", "燕麦", "红薯", "紫薯", "牛奶", "酸奶", "鸡蛋",
)


def _mentions_food_ingredients(text) -> bool:
    """消息里是否点到了具体食材（纯食材消息也要走候选阶段）。"""
    raw = str(text or "")
    return any(word in raw for word in _INGREDIENT_KEYWORDS)


def _is_candidate_turn(messages) -> bool:
    """本轮是否走「候选清单」阶段（只给编号候选，不出卡片不配图）。"""
    intent = _classify_turn_intent(messages)
    if intent not in ("recommend", "other"):
        return False
    raw = _latest_user_text(messages, strip_internal=False)
    text = _current_request_text(raw)
    if not text or _is_specific_dish_request(text):
        return False
    # 路由已经注入配图开关时，正文若是“番茄炒蛋”这类裸菜名，说明本菜已选定，
    # 不能再把菜名本身误当泛推荐。只有开关外仍是“推荐几道菜”这类泛意图才保留候选阶段。
    if (
        any(marker in raw for marker in _INTERNAL_REQUEST_MARKERS)
        and not _is_generic_dish_name(text)
    ):
        return False
    # 健康问答（能不能吃 / 适合吗）不是点菜需求，保持原对话链路
    if re.search(r"(能不能吃|可不可以吃|能吃|能喝|适合吃|该不该吃|要不要吃|可以吃吗)", text):
        return False
    # 闲聊（intent=other）只有真的点了食材才算点菜需求，避免「你好」也被列一顿候选
    if intent == "other" and not _mentions_food_ingredients(text):
        return False
    return True


_CN_NUMERALS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _candidate_number(token: str):
    """把中文/阿拉伯序号转成整数，转不出来返回 None。"""
    token = str(token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_NUMERALS:
        return _CN_NUMERALS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_NUMERALS:
        return 10 + _CN_NUMERALS[token[1]]
    if token.endswith("十") and len(token) == 2 and token[0] in _CN_NUMERALS:
        return _CN_NUMERALS[token[0]] * 10
    return None


def parse_candidate_index(text):
    """把「就第2个 / 第二个 / 第2道 / 选2」解析成 1 起始的候选序号（解析不出返回 None）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = re.search(r"第\s*([0-9]+|[一二两三四五六七八九十]+)\s*[个道款样号份]", raw)
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    # 「选2 / 就要2 / 做2」这类极短指令才当序号，避免把「做2个菜」误判成选第2道
    if len(raw) <= 12:
        match = re.search(r"(?:选|就要|就选|就做|要|做)\s*([0-9]{1,2})\s*(?:号|个|道)?$", raw)
        if match:
            number = _candidate_number(match.group(1))
            if number and number > 0:
                return number
    # 「3吧 / 3， / 3。」这类口语选定：裸序号 + 语气词或标点收尾（用户真实说法是「3吧，但是可以辣一点」）。
    # 必须紧跟语气词/标点才算，否则「做2个菜」「3个人」会被误判成选第 2/3 道；
    # 这里只做形态识别，真正的锚点校验在调用方——_classify_turn_intent 要求 has_prior_candidates、
    # _resolve_picked_candidate 要求会话里真有候选清单，所以「第9个」这种越界序号不会被认下。
    match = re.match(r"^\s*([0-9]{1,2})\s*(?:吧|啊|呀|呢|哦|，|,|。|！|!)", raw)
    if match:
        number = _candidate_number(match.group(1))
        if number and number > 0:
            return number
    return None


def _is_internal_user_turn(message) -> bool:
    """系统自动注入的「假用户消息」：历史摘要与健康护栏审核。

    它们不是新的用户对话轮次，而是同一轮的内部续跑。判定必须与 `_latest_user_text`
    的跳过口径完全一致，否则会出现「正文取到了本轮需求、锚点却截断在内部消息上」的错位。
    """
    content = str(getattr(message, "content", "") or "")
    return content.startswith(("[历史对话摘要", "[健康护栏审核"))


def _recent_candidates(messages, limit=8):
    """取当前轮之前最近一轮登记的候选菜名（两阶段交互第一跳的确定性锚点）。

    只查最新一条**真实**用户消息之前的历史：当前轮生成的正文不能遮蔽上一轮候选，
    但候选之后若已经发生过别的对话轮次，也不能再把旧序号误认成本轮选定。

    内部消息（历史摘要 / 健康护栏审核）不算新轮次 —— 否则一轮里只要发生过护栏重试，
    候选锚点就会被截断掉：回溯时先撞上重试后的普通 AI 回答（非候选 payload）→ 返回空
    → 序号指代失效 → 走到 verify_route 时被判成「非点菜轮」直通纯文本，卡片和配图一起消失。
    实测复现：「饭点推荐 → 2吧，但是我的父亲高血压，可以吃这个吗」。
    """
    history = list(messages or [])
    for index in range(len(history) - 1, -1, -1):
        if isinstance(history[index], HumanMessage) and not _is_internal_user_turn(history[index]):
            history = history[:index]
            break
    for m in reversed(history):
        if not isinstance(m, AIMessage) or getattr(m, "tool_calls", None):
            continue
        raw = str(getattr(m, "content", "") or "").strip()
        if not raw.startswith("{"):
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        if str(data.get("answer_kind") or "") != "candidates":
            return []
        names = [str(n).strip() for n in (data.get("candidates") or []) if str(n).strip()]
        if names:
            return names[:limit]
        return []
    return []


def resolve_candidate_pick(messages):
    """把用户这一轮的序号指代解析成候选里的 (序号, 菜名)；解析不出返回 None。"""
    index = parse_candidate_index(_latest_user_text(messages))
    if not index:
        return None
    candidates = _recent_candidates(messages)
    if not candidates or index > len(candidates):
        return None
    return index, candidates[index - 1]


# 带健康/口味调整语气的词：出现这些说明是在「调整这道菜」（追问或改菜），而不是「选定」。
# `_classify_turn_intent` 与 `_is_dish_pick_turn` 共用这一份口径，避免两层判定漂移。
_DISH_ADJUST_WORDS = (
    "清淡", "少", "淡", "不要", "别", "盐", "油", "热量", "钠", "糖", "脂肪",
    "能不能", "可以吗", "适合吗", "怎么吃",
)


def _is_dish_pick_turn(messages) -> bool:
    """本轮是否「真的选定了一道菜」——给 `_is_ask_turn` 开一个窄口子用。

    背景：「2吧，可以更酸一点，但是我的父亲高血压，可以吃这个吗」这类轮次里，
    正文（opening）确实以问句收尾，但它同时是一次明确的选定；整体当追问挡掉，
    就会出现「用户已经确认了，却没有卡片、没有图片」。

    口子必须窄：没有菜名锚点的纯提问（「想确认一下，你更想吃清淡的还是重口的？」）
    依然要被挡住，否则会答非所问地弹卡片。
    """
    text = _latest_user_text(messages)
    if not text:
        return False
    # 序号选定（「就第2个」「2吧」）本身无歧义：只要真有候选锚点就算选定
    if resolve_candidate_pick(messages):
        return True
    # 正文点名了候选清单/最近卡片里的菜也算选定；带调整语气的算追问（与 _classify_turn_intent 同口径）
    anchor_names = list(_recent_candidates(messages)) + list(_recent_recipe_names(messages))
    if not anchor_names or not _mentions_recent_recipe(text, anchor_names):
        return False
    return not any(word in text for word in _DISH_ADJUST_WORDS)


def _extract_candidate_names(text) -> list:
    """从正文里解析「1. 菜名 —— 理由」形式的编号候选清单。

    候选序号必须与用户看到的正文一致，所以以正文为准解析，
    而不是另外让模型再生成一份清单（两份清单顺序可能不同，「第2个」就会指错菜）。
    """
    names = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^(?:[-*•]\s*)?(\d{1,2})\s*[.、)）．:：]\s*(.+)$", line)
        if not match:
            continue
        body = match.group(2).strip()
        # 菜名与推荐理由之间用 —— / - / ： 等分隔，取分隔符前的部分当菜名
        body = re.split(r"\s*(?:——|—|--|–|:|：|\||｜)\s*", body, maxsplit=1)[0]
        name = body.strip().strip("*`「」『』\"'“”").strip()
        name = re.sub(r"[，。！？；;,!.?;]+$", "", name).strip()
        if not (2 <= len(name) <= 14):
            continue
        if name in names:
            continue
        names.append(name)
    return names[:8]


def _candidate_source_text(messages) -> str:
    """汇总本轮已经展示过的所有助手正文，供候选锚点解析。

    健康护栏可能打回重生成：第一段已经流式展示了编号候选，第二段只修成
    一道安全菜。只解析最后一条会丢掉用户真正看到的候选序号，因此这里按
    本轮真实用户消息之后的全部助手正文汇总，编号去重仍由候选解析负责。
    """
    history = list(messages or [])
    start = 0
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and not str(message.content).startswith("[健康护栏审核")
        ):
            start = index + 1
            break
    parts = []
    for message in history[start:]:
        if not isinstance(message, AIMessage) or getattr(message, "tool_calls", None):
            continue
        text = _message_text(message).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _filter_candidate_names(names) -> list:
    """候选菜名也要过过敏原硬审计：清单里不能出现用户碰不得的菜。"""
    kept = []
    allergens = _allergens_for_audit()
    for name in names or []:
        dish = str(name or "").strip()
        if not dish:
            continue
        if allergens:
            try:
                violations = audit_allergens(dish, allergens, use_optional=True)
            except Exception:
                violations = []
            if violations:
                continue
        kept.append(dish)
    return kept


def _looks_like_home_service_request(text: str) -> bool:
    text = str(text or "")
    markers = ("上门", "到家服务", "私厨", "厨师到家", "请厨师", "预约厨师", "上门做")
    return any(marker in text for marker in markers)


def _classify_turn_intent(messages) -> str:
    """确定性意图门：recommend | confirm_one | change_one | followup | restaurant | home_service | other。"""
    text = _latest_user_text(messages)
    if not text:
        return "other"
    current = _current_request_text(text)
    if _looks_like_home_service_request(current):
        return "home_service"
    if _is_restaurant_ordering_scene(current):
        return "restaurant"

    recipe_names = _recent_recipe_names(messages)
    candidates = _recent_candidates(messages)
    has_prior_recipe = bool(recipe_names)
    has_prior_candidates = bool(candidates)

    change_words = (
        "没胃口", "不想吃这个", "不想吃了", "换一道", "换一个", "换别的",
        "换成", "改成", "做成", "换做", "改做", "改为", "变成", "没食欲",
    )
    recipe_replacement_words = ("面", "汤", "菜", "饭", "粥", "粉", "肉", "鱼", "鸡", "牛", "虾", "豆腐")
    broad_change_words = ("没胃口", "不想吃这个", "不想吃了", "换一道", "换一个", "换别的", "没食欲")
    if any(word in current for word in broad_change_words) or (
        any(word in current for word in change_words)
        and any(word in current for word in recipe_replacement_words)
    ):
        return "change_one"

    # 从候选列表里选（「就第2个」「第二个」）必须真有候选锚点才算确认，
    # 避免「第2个问题」这类无关序号被当成点菜。
    if has_prior_candidates and parse_candidate_index(current):
        return "confirm_one"

    confirm_words = ("就做", "就吃", "来这个", "做这个", "吃这个", "定这个", "选这个", "就它", "就这道")
    if any(word in current for word in confirm_words) or (
        _has_recipe_index_ref(current)
        and (has_prior_recipe or has_prior_candidates)
        and (
            _mentions_recent_recipe(current, recipe_names)
            or _mentions_recent_recipe(current, candidates)
        )
    ):
        return "confirm_one"

    followup_words = ("清淡", "少盐", "少油", "不要", "别放", "能不能", "可以吗", "适合吗", "热量", "钠", "糖", "脂肪", "怎么吃")
    if (has_prior_recipe or has_prior_candidates) and not _mentions_recent_recipe(current, recipe_names) and any(word in current for word in followup_words):
        return "followup"

    # 直接点名候选里的一道菜（「就要青菜豆腐汤」）同样是选定，但必须排在追问之后：
    # 带健康调整语气的（「青菜豆腐汤少放点盐」）算追问，不算选定。
    adjust_words = ("清淡", "少", "淡", "不要", "别", "盐", "油", "热量", "钠", "糖", "脂肪", "能不能", "可以吗", "适合吗", "怎么吃")
    if (
        has_prior_candidates
        and _mentions_recent_recipe(current, candidates)
        and len(current) <= 14
        and not any(word in current for word in adjust_words)
    ):
        return "confirm_one"

    if _looks_like_dining_request(current):
        return "recommend"
    return "other"


def _is_new_ingredient_image_request(text):
    """判断本轮图片是新的食材输入，还是对当前菜品的修改。"""
    # 调用此函数的前提是消息中已经包含图片。
    # 图片本身就是用户明确提供的新视觉输入，因此直接建立新的食材主题。
    # 普通的“清淡一点”“换成猪肉”等追问没有图片，不会进入这里，
    # 仍然通过 checkpoint 保留当前菜品记忆。
    return True


def _latest_user_index(messages):
    """返回最近一条真实用户消息的位置，跳过历史摘要消息。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and not str(message.content).startswith("[健康护栏审核")
        ):
            return index
    return None


def _latest_new_image_index(messages):
    """返回最近一次新食材图片的起点，作为当前菜品主题边界。"""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, HumanMessage)
            and not str(message.content).startswith("[历史对话摘要")
            and _message_has_image(message)
            and _is_new_ingredient_image_request(_message_text(message))
        ):
            return index
    return None


def _messages_for_current_turn(messages, isolate_old_context=False):
    """按需要取当前轮消息，避免新图片请求混入旧菜品的工具结果。"""
    if not isolate_old_context:
        return messages
    index = _latest_new_image_index(messages)
    if index is None:
        index = _latest_user_index(messages)
    if index is None:
        return messages
    return messages[index:]


def _current_turn_has_tool_result(messages) -> bool:
    """本轮是否已经执行过工具，避免强制搜索在回边后重复触发。"""
    start = _latest_user_index(messages)
    if start is None:
        return False
    return any(isinstance(m, ToolMessage) for m in messages[start + 1:])


@trace_node("chef_think")
def chef_agent_node(state: MessagesState):
    messages = state["messages"]#已经被压缩过后的4种消息类的消息
    # 前置插入系统提示词，再追加历史对话消息（先清掉孤儿 ToolMessage 防 API 400）
    latest_text = _latest_user_text(messages)
    latest_has_image = _latest_user_has_image(messages)
    # 强制搜索只负责本轮第一次决策；工具结果回流到 chef_think 后必须交给
    # LLM 基于结果收口，否则同一轮会反复创建同样的 web_search，直到耗尽预算。
    if _should_force_web_search(latest_text) and not _current_turn_has_tool_result(messages):
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": f"forced-web-search-{len(messages)}",
                            "name": "web_search",
                            "args": {"query": latest_text},
                        }
                    ],
                )
            ]
        }
    is_new_image_request = (
        latest_has_image and _is_new_ingredient_image_request(latest_text)
    )
    prompt_content = SYSTEM_PROMPT
    # 两阶段点菜：泛推荐轮出候选清单（不出卡片），其余轮次保持单菜规则。
    if _is_candidate_turn(messages):
        prompt_content += _candidate_list_rule(messages)
    elif not _wants_multiple_recipes(messages):
        prompt_content += SINGLE_RECIPE_RULE
    # 用户从上一轮候选清单里选了第 N 道：把菜名确定性注入本轮提示词，
    # 保证卡片就是用户选的那道菜，不靠模型自己猜「第2个」指谁。
    picked = resolve_candidate_pick(messages)
    if picked:
        prompt_content += (
            f"\n\n【本轮已选定的候选】用户从上一轮的候选清单里选了第{picked[0]}道：{picked[1]}。"
            "本轮只输出这一道菜的完整做法，不要换菜、不要再列候选清单。"
        )
    if is_new_image_request:
        prompt_content += (
            "\n\n【新食材图片优先规则】"
            "本轮用户上传的是新的食材图片，并且是在询问新的菜品推荐。"
            "必须以本轮图片识别出的食材为最高优先级，忽略历史中的当前菜名、旧食材和旧菜谱。"
            "先重新识别本轮图片，再基于本轮食材调用搜索工具。"
            "不得因为历史摘要中存在上一道菜，就继续生成上一道菜。"
        )
    prompt_msg = SystemMessage(content=prompt_content)#保存字符串提示词
    model_messages = _messages_for_current_turn(
        messages,
        isolate_old_context=_latest_new_image_index(messages) is not None,
    )
    payload = [prompt_msg] + _drop_orphan_tool_messages(model_messages)#提示词+历史对话消息，中括号是转换成列表调用的涵数是把孤立的toolmessage删除
    # 上游 LLM 偶发 502/超时，加重试避免整轮对话直接 500 崩掉
    last_err = None
    for attempt in range(3):
        try:
            resp = llm_with_tools.invoke(payload)#就是给LLM的所有上下文消息让他生成怎么调用和调用什么工具的消息 带tool_calls的 AI 消息
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError) as e:
            last_err = e#存储错误
            time.sleep(1 + attempt)  # 退避 1s / 2s 再试，但只试3次
    else:
        raise last_err  # 3 次都失败才真正抛出错误
    return {"messages": [resp]}

# 节点2：工具执行节点（自动调用 web_search / get_file）产出 ToolMessage
tool_executor = ToolNode(tools)#直接执行不思考


def _latest_tool_call_count(messages) -> int:
    """取最近一条 AI 工具调用消息里的 tool_call 数量。"""
    for m in reversed(messages or []):
        if isinstance(m, AIMessage):
            return len(getattr(m, "tool_calls", None) or [])
    return 0


def _turn_tool_budget(messages) -> int:
    """本轮的工具调用预算：常规 4 次；「已选定一道菜」的轮次收紧到 2 次。

    为什么单独收紧：用户选完菜顺带问一句健康问题（「2吧，可以更酸一点，但是我的父亲
    高血压，可以吃这个吗」）时，模型倾向反复联网检索，把预算搜爆后走收口逻辑，
    卡片和图片一起消失。判定锚点必须确定性 —— 复用 `_is_dish_pick_turn`
    （序号选定 / 正文点名候选或最近卡片里的菜），不依赖模型自述。
    """
    if _is_dish_pick_turn(messages):
        return PICK_TURN_TOOL_BUDGET
    return MAX_TOOL_CALLS_PER_TURN


def chef_route_with_tool_budget(state: "ChefState") -> str:
    """chef_think 后的路由：有工具且未超预算才执行工具，否则优雅收尾。"""
    route = tools_condition(state)
    if route != "tools":
        return "verify"
    used = int(state.get("tool_calls_in_turn", 0) or 0)
    budget = int(state.get("tool_budget", 0) or 0) or MAX_TOOL_CALLS_PER_TURN
    pending = _latest_tool_call_count(state.get("messages", []))
    if used + max(pending, 1) > budget:
        return "tool_budget_exhausted"
    return "tools"


@trace_node("run_tools")
def run_tools_node(state: "ChefState"):
    """执行工具并累计本轮工具调用次数。"""
    result = tool_executor.invoke(state)
    used = int(state.get("tool_calls_in_turn", 0) or 0)
    return {
        **(result or {}),
        "tool_calls_in_turn": used + max(_latest_tool_call_count(state.get("messages", [])), 1),
    }


@trace_node("tool_budget_finalize")
def tool_budget_finalize_node(state: "ChefState"):
    """工具预算耗尽时，删除未执行 tool_call，给用户一个基于已有信息的普通结论。"""
    messages = state.get("messages", [])
    removals = []
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            if getattr(m, "id", None):
                removals.append(RemoveMessage(id=m.id))
            break
    user_text = _current_request_text(_latest_user_text(messages))
    used = int(state.get("tool_calls_in_turn", 0) or 0)
    fallback = (
        f"我已经完成了 {used} 次资料检索，为了避免继续空转，先基于现有信息给你收口："
        "优先选少油少盐、食材明确、做法简单的一道；如果涉及慢病、腹泻、痛风或控糖，"
        "避开油炸、重辣、冷饮和高糖饮料。"
    )
    if user_text:
        fallback += f"\n\n针对你这次说的「{user_text[:60]}」，我会按这些边界给出稳妥建议。"

    # 预算耗尽不是错误：用已有工具结果做一次不带工具的收口生成。若上游仍失败，
    # 或生成内容命中健康硬禁忌，则退回确定性的安全文案，绝不让本轮落库为空。
    content = fallback
    evidence = _tool_result_evidence(messages)
    if evidence:
        try:
            response = llm.invoke([
                SystemMessage(content=(
                    "你是小膳管家。请只根据用户需求和已经检索到的资料，直接给出最终中文建议。"
                    "不要再索取或调用工具，不要描述内部流程；优先用一道可执行、少油少盐的菜收口。"
                    "资料不足时明确说明不确定，不要编造。"
                )),
                HumanMessage(content=(
                    f"用户需求：{user_text or '继续完成本轮建议'}\n\n"
                    f"已检索到的工具资料：\n{evidence}"
                )),
            ])
            candidate = response.content
            if isinstance(candidate, list):
                candidate = "".join(
                    str(block.get("text", ""))
                    for block in candidate
                    if isinstance(block, dict)
                )
            candidate = str(candidate or "").strip()
            if candidate and not audit(candidate, _merged_conditions(user_text)):
                content = candidate
        except Exception:
            pass
    return {
        "messages": removals + [AIMessage(content=content)],
        "tool_budget_exhausted": True,
        "verify_status": "ok",
    }


def _tool_result_evidence(messages, max_items=3, max_chars=1800) -> str:
    """把最近工具结果压成供收口模型使用的短证据，避免把原始长 JSON 再灌满上下文。"""
    blocks = []
    for message in reversed(messages or []):
        if not isinstance(message, ToolMessage):
            continue
        raw = str(message.content or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            raw = str(parsed.get("text") or parsed.get("content") or raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            continue
        name = str(getattr(message, "name", "") or "tool")
        blocks.append(f"[{name}] {raw}")
        if len(blocks) >= max_items:
            break
    return "\n\n".join(reversed(blocks))[:max_chars]

# --------------------------------------------------------------------------- #
# 从整个对话消息列表里，提取、拼接给结构化 LLM 使用的 Prompt 上下文
# 同时抓取本次搜索拿到的图片链接 + 是否为 AI 生成图两个标记
# 返回三个值：组装好的文本上下文、真实图片URL、是否AI生成图片布尔值
# --------------------------------------------------------------------------- #
def _build_structure_context(messages, isolate_old_context=False):#解析出了图片链接和来源和文本是进入LCEL之前的准备工作
    """给结构化链组装上下文：最近一条真实用户需求 + 本轮 web_search 的搜索结果。
    返回 (context_text, real_image_url, real_image_ai)：
      - real_image_url：最近一次搜索真实拿到的图片链接（可能为 None），
        作为"有没有图"的代码级依据，不信模型口头说法；
      - real_image_ai：该图是否由通义万相 AI 生成（image_source=="ai"），
        用于下游透明标注「AI 生成示意图」，防止把生成图当用户实拍图误导。
    注意：real_image_url 与 real_image_ai 始终成对赋值，保证"有图"与"是否 AI 图"口径一致。"""
    context_messages = _messages_for_current_turn(
        messages,
        isolate_old_context=isolate_old_context,
    )
    parts = []#存储上下文的纯文本
    real_image_url = None#存储真实图片链接
    real_image_ai = False#存储真实图片是否由 AI 生成
    # 最近一条真实用户消息（跳过压缩节点注入的"历史对话摘要"，图文混合消息只取文字）
    for m in reversed(context_messages):#是用户文档并且不是历史对话摘要的消息
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要") and not str(m.content).startswith("[健康护栏审核"):
            if isinstance(m.content, list):#图文混合：图片已识别进对话，只取文字部分
                text = " ".join(#用户发图 + 文字提问时，只保留文字需求，图片不塞进给 LLM 的文本上下文
                    part.get("text", "") for part in m.content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                text = str(m.content)
            parts.append("用户需求：" + text)
            break
    # 把本轮 Agent 已经确认的自然语言回答交给结构化模型。
    # 这样卡片使用同一轮已经识别出的菜名，不会根据旧搜索结果重新猜一道菜。
    for m in reversed(context_messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            ai_text = str(m.content).strip()
            if ai_text and not ai_text.startswith('{"opening"'):
                _excerpt = ai_text[:800] + ("…（正文已截断）" if len(ai_text) > 800 else "")
                parts.append("本轮 Agent 已确认的回答（菜名和食材以此为准）：\n" + _excerpt)
            break
    # 本轮所有 web_search 工具返回（JSON 字符串：text 搜索结果 + image_url 图片 + image_source 图源）
    search_blocks = []#2.有连坐删除，删的时候会把工具的返回结果也会删除不搞混
    for m in context_messages:#把工具返回的搜索结果都塞进parts不搞混，只取最近2条控制结构化上下文长度
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "web_search":
            content = str(m.content)
            search_blocks.append(content)#存储搜索结果
            try:#新格式 {text, image_url, image_source}；旧格式是纯文本，json 解析失败就跳过
                parsed = json.loads(content)#将存的有用的变为字典
                img = parsed.get("image_url")#拿地址
                src = parsed.get("image_source")#拿来源
                # 成对赋值：有图才更新图源标记，保证"图"与"是否 AI 图"一致
                if img:
                    real_image_url = img#后出现的覆盖前面的，留下最近一次
                    real_image_ai = (src == "ai")#仅当该次返回明确标记为 AI 生成才置 True自动的
            except (ValueError, AttributeError):
                pass
    if search_blocks:
        _trimmed = [b[:1500] for b in search_blocks[-2:]]
        parts.append(
            "搜索结果（每条是 JSON：text 为搜索文本、image_url 为成品图链接或 null、"
            "image_source 为图源 real/ai）：\n"
            + "\n\n".join(_trimmed)#最多取最近2次搜索，单条截断避免结构化阶段上下文过重
        )
    # 本轮所有 nutrition_kb_search 工具返回（权威健康依据，JSON 含 source 文件名与命中片段 text）
    kb_blocks = []
    for m in context_messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "nutrition_kb_search":
            kb_blocks.append(str(m.content))
    if kb_blocks:
        parts.append(
            "权威健康依据检索结果（来自 nutrition_kb_search，每条 JSON 含 source 文件名与命中片段 text）：\n"
            + "\n\n".join(kb_blocks[-3:])
        )
    return "\n\n".join(parts), real_image_url, real_image_ai


def _is_dining_scene(text: str) -> bool:
    """T2-P3：外出就餐场景 = 不输出烹饪步骤。关键词命中即视为该场景。"""
    import re as _re
    return bool(_re.search(r"食堂|外卖|外吃|外出就餐|点餐|吃饭|省钱吃|怎么吃|餐厅|档口|套餐|就餐", text))


def _looks_like_dining_request(text: str) -> bool:
    """识别做菜、点餐、饮食建议等饮食相关请求。"""
    text = str(text or "").strip()
    if not text:
        return False
    markers = (
        "做饭", "做菜", "菜谱", "食谱", "菜品", "食材", "配方", "烹饪", "做法",
        "帮我做", "做道", "做个", "做一份", "做一下", "来道", "来个",
        "推荐", "吃", "饭", "餐", "早餐", "午餐", "晚餐", "夜宵", "外卖", "点餐", "食堂",
        "汤面", "面条", "米粉", "米线", "炒肉", "家常菜",
        "餐厅", "冰箱", "营养", "热量", "减脂", "控糖", "高血压", "糖尿病",
        "痛风", "尿酸", "健康饮食", "附近吃什么",
    )
    return any(marker in text for marker in markers)


def _is_non_food_followup(text: str) -> bool:
    """识别“锂电池怎么没有用上”这类非食材追问，避免再附一张菜谱卡片。"""
    raw = str(text or "").strip()
    if not raw or _mentions_food_ingredients(raw) or _looks_like_dining_request(raw):
        return False
    return bool(re.search(
        r"(?:怎么|为什么|咋)?(?:没有|没|未).{0,4}(?:用上|用|算上|放进去|考虑)",
        raw,
    ))


def _is_restaurant_ordering_scene(text: str) -> bool:
    """餐厅/外食点餐请求只保留纯文本，不进入菜谱卡片结构化。"""
    text = str(text or "").strip()
    if not text:
        return False
    restaurant_markers = (
        "餐厅", "饭店", "饭馆", "店里", "到店", "堂食", "外食", "外吃", "外出就餐",
        "出去吃", "出去吃饭", "在外吃", "在外面吃", "下馆子", "聚餐",
        "点餐", "点单", "菜单", "套餐", "档口", "食堂", "外卖", "附近",
    )
    restaurant_context = ("店", "餐厅", "饭店", "食堂", "外卖", "附近")
    signature_words = ("招牌", "推荐几道菜", "推荐几个菜", "点什么菜")
    cooking_markers = (
        "做法", "怎么做", "菜谱", "食谱", "烹饪", "开火", "下锅",
        "食材", "冰箱", "在家做", "自己做",
    )
    if any(marker in text for marker in cooking_markers):
        return False
    return any(marker in text for marker in restaurant_markers) or (
        any(word in text for word in signature_words)
        and any(ctx in text for ctx in restaurant_context)
    )


def _latest_user_text(messages, *, strip_internal=True):
    """提取本轮用户的文字需求，图文消息只取文字部分。"""
    for m in reversed(messages):
        if (
            isinstance(m, HumanMessage)
            and not str(m.content).startswith("[历史对话摘要")
            and not str(m.content).startswith("[健康护栏审核")
        ):
            text = _message_text(m)
            return _current_request_text(text) if strip_internal else text
    return ""


def _should_force_web_search(text: str) -> bool:
    """把明显需要联网查网页/做法/最新信息的请求，提前转成 web_search 工具调用。"""
    text = str(text or "").strip()
    if not text:
        return False
    if _is_dining_scene(text):
        return False
    if any(word in text for word in ("高血压", "糖尿病", "血糖", "尿酸", "痛风", "减脂", "控糖", "低嘌呤", "能不能吃", "营养", "热量", "钠")):
        return False
    force_words = (
        "联网", "网页", "网页搜索", "搜索一下", "查一下", "查查", "最新", "最新的",
        "做法", "菜谱", "怎么做", "家常菜", "快手菜", "食材搭配", "配方", "推荐做法",
        "网上", "上网", "外网", "百度", "Tavily",
    )
    return any(word in text for word in force_words)


def _wants_multiple_recipes(messages):
    """只有用户明确要求多个选择时才开启多菜模式。"""
    text = _latest_user_text(messages)
    multiple_words = (
        "多几道", "多道菜", "几道菜", "多个选择", "多种选择",
        "供我选择", "分别推荐", "多推荐几道", "多生成几道",
    )
    return any(word in text for word in multiple_words)


def _search_recipe_image(recipe_name):
    """按最终菜名搜索图片，返回 (url, 是否AI生成)。"""
    try:
        image_url, image_source = find_recipe_image(recipe_name, allow_ai_fallback=False)
        return image_url, image_source == "ai"
    except Exception:
        return None, False


def _matrix_cell(row, key: str) -> str:
    """从 DishMatrixItem（pydantic）或 dict 里取字段，便于单测直接喂 dict。"""
    if isinstance(row, dict):
        return str(row.get(key) or "")
    return str(getattr(row, key, "") or "")


def _family_conflict_guardrails(dish_matrix) -> list:
    """把分餐矩阵的冲突抬进右栏护栏，保证「矩阵说什么、护栏就显示什么」。

    这是安全表达问题而不只是 UI 问题：矩阵已判定某成员不可吃，
    右栏却显示「未触发额外健康约束」，等于当着用户的面自相矛盾。
    """
    from allergen_rules import UNRESOLVED_ALLERGEN_ADVICE

    items = []
    for row in dish_matrix or []:
        verdict = _matrix_cell(row, "verdict")
        member = _matrix_cell(row, "member") or "成员"
        dish = _matrix_cell(row, "dish") or "本轮主菜"
        reason = _matrix_cell(row, "reason")
        if verdict == "不可吃":
            items.append(GuardrailItem(
                condition=f"同餐冲突:{member}",
                rule=f"{member}不可吃「{dish}」，必须单独替换并避免交叉接触",
                status="member_conflict",
                reason=reason or f"{member}的硬约束与本轮主菜冲突；需单独替换并避免交叉接触",
            ))
        elif verdict == "待确认":
            items.append(GuardrailItem(
                condition=f"过敏原待确认:{member}",
                rule=f"{member}档案里的过敏原未纳入标准规则，仅按原文提醒",
                status="warn",
                reason=reason or UNRESOLVED_ALLERGEN_ADVICE,
            ))
    return items


def _build_guardrails(user_text, verify_status, verify_violated, dish_matrix=None):
    """依据 verify 节点真实审计结论，为前端右栏拼出『本轮健康护栏』列表（健康链可见化的核心）。

    - 对每个检测到的健康标签，给出 pass / warn / adjusted 结论与一句理由；
    - 不依赖 LLM，以 verify 的确定性审计口径为准，避免关键字子串误判
      （如『少盐』被『盐』误命中导致合规方案被误标为已调整）；
    - 与 verify_answer_node 共用同一套 RULES，口径一致。
    """
    conditions = _merged_conditions(user_text)
    for allergen in _allergens_for_audit():
        condition = f"过敏原:{allergen_label(allergen)}"
        if condition not in conditions:
            conditions.append(condition)
    # blocked 场景兜底：本轮实际命中的过敏原也并进来。
    # 理由——档案可能在审计后变更，只靠 _allergens_for_audit() 会漏，
    # 而"拦截了却不在右栏显示"等于把护栏证据弄丢。
    for violation in verify_violated or []:
        text = str(violation)
        if text.startswith("过敏原:") and text not in conditions:
            conditions.append(text)
    violated = set(verify_violated or [])
    items = []
    for cond in conditions:
        rule_msg = RULES.get(cond, {}).get("message", "")
        if not rule_msg and str(cond).startswith("过敏原:"):
            rule_msg = f"必须完全不含{str(cond).split(':', 1)[-1]}，包括调料与隐含来源"
        # 分支顺序不可调换：blocked 必须先判，否则会落进 adjusted 分支，
        # 把"拒绝推荐"说成"已自动调整至合规"——那是不实表述。
        if cond in violated and verify_status == "blocked":
            status, reason = "blocked", "已拦截，本轮未输出含该过敏原的菜品：" + rule_msg
        elif cond in violated and verify_status == "degraded":
            status, reason = "warn", "经多次重生成仍有需注意项，请谨慎：" + rule_msg
        elif cond in violated:
            # 曾被硬护栏命中、但最终通过了审计（已重生成至合规）
            status, reason = "adjusted", "初始方案命中硬禁忌，已由健康护栏自动调整至合规：" + rule_msg
        else:
            status, reason = "pass", "已符合" + cond + "膳食原则"
        items.append(GuardrailItem(condition=cond, rule=rule_msg, status=status, reason=reason))
    # 同餐成员冲突必须以矩阵为准：没有健康标签不等于没有冲突。
    items.extend(_family_conflict_guardrails(dish_matrix))
    # 档案不可用时护栏必然降级：必须让用户看见，不能让"没有约束"冒充"全部通过"。
    _, profile_error = _profile_health()
    if profile_error:
        items.append(GuardrailItem(
            condition="健康档案不可用",
            rule="本轮护栏已降级：档案里的健康约束与过敏原都没有生效",
            status="warn",
            reason=f"{profile_error}。请重新建档或在「家庭成员」中修正后再决策。",
        ))
    return items


def _candidate_list_payload(opening, names, latest_text, state):
    """两阶段点菜第一阶段的候选清单 payload（recipes 为空 = 前端根本不出卡片）。

    候选菜名以用户真正看到的正文编号为准，这样「第2个」的映射不会指错菜。
    返回 None 表示拿不到候选——此时保持纯文本，绝不在推荐阶段硬塞一张卡片。
    """
    names = [str(name).strip() for name in (names or []) if str(name).strip()]
    if len(names) < 2:
        return None
    return {
        "opening": opening,
        "answer_kind": "candidates",
        "candidates": names,
        "recipes": [],
        "image_url": None,
        "image_ai_generated": False,
        "image_requested": False,
        "image_note": "",
        "chef_tip": "回复序号就行，我再把这一道的完整做法给你。",
        "sources": [],
        "health_lights": [],
        "guardrails": [
            item.model_dump()
            for item in _build_guardrails(
                latest_text,
                state.get("verify_status", ""),
                state.get("verify_violated", []),
            )
        ],
        "primary_member": _active_member_name(),
    }


@trace_node("structure_answer")
def structure_answer_node(state: MessagesState):#结构化回答节点
    messages = state["messages"]
    # chef_think 最后一轮的自然语言回答 = 流式已经推给前端的正文，原样保留进 opening
    opening = ""
    if messages and isinstance(messages[-1], AIMessage):
        # 这里已经传入前端了，所以比结构化卡片快；但模型偶尔直接吐 JSON，
        # 必须清洗后再当正文用（否则气泡和卡片里会出现一坨 JSON）。
        opening = _clean_opening_text(messages[-1].content)
        #context是纯文本给了LCEL结构化链，其他的图片的链接和图片的来源在下文传出来的answer来赋值
    latest_text = _latest_user_text(messages)
    turn_intent = _classify_turn_intent(messages)
    if turn_intent in ("restaurant", "home_service"):
        return {"messages": []}
    if _is_non_food_followup(latest_text):
        return {"messages": []}
    # 询问/澄清轮次不出卡片：正文整段是一条追问时保持纯文本，
    # 否则「先问用户想法」和「直接给卡片」会同时发生（语义自相矛盾）。
    # 例外：「已经选定了一道菜，只是顺带问一句」不算追问——否则用户确认完却拿不到卡片和图。
    if _is_ask_turn(opening) and not _is_dish_pick_turn(messages):
        return {"messages": []}
    # 两阶段点菜第一阶段标记：泛推荐轮只出候选清单，不出卡片、不配图。
    # 但判定结果要等到「结构化过敏原复核」之后才生效——安全拦截优先于产品形态。
    is_candidate_turn = _is_candidate_turn(messages)
    wants_images = _wants_recipe_images(messages)
    is_new_image_request = (
        _latest_user_has_image(messages)
        and _is_new_ingredient_image_request(latest_text)
    )
    context, real_image_url, real_image_ai = _build_structure_context(
        messages,
        isolate_old_context=_latest_new_image_index(messages) is not None,
    )#解包拿到
    allow_multiple = _wants_multiple_recipes(messages)
    if not context.strip():#没有可整理的上下文（理论上不会走到这），直接结束
        return {"messages": []}
    # 序号选定轮要把「用户选了哪一道」当硬约束喂给结构化链：
    # 否则收口/降级路径下模型会自由发挥，卡片菜名与用户选的那道对不上（实测「选第2道」
    # 却出成另一个名字）。候选菜名以用户看到的编号正文为准，这里只是把它显式钉死。
    picked = resolve_candidate_pick(messages)
    if picked:
        context = (
            f"【本轮已选定：{picked[1]}（候选第{picked[0]}道）——只输出这一道，不得换菜名】\n\n"
            f"{context}"
        )
    try:#结构化链带「格式自动重试」：解析失败会回灌 LLM 修正，重试耗尽才降级
        answer = build_structured_answer(context)#会返回一个实例
        answer = rank_recipes(answer, allow_multiple=allow_multiple)
        # 自然语言已通过一次审计，但结构化模型仍可能改写食材或调料。
        # 卡片输出前再审一次，命中过敏原时直接放弃卡片，保留已通过审计的正文。
        structured_text = "\n".join(
            " ".join([
                recipe.name,
                recipe.intro,
                " ".join(
                    f"{seasoning.name} {seasoning.amount}"
                    for seasoning in recipe.seasonings
                ),
                " ".join(recipe.steps),
            ])
            for recipe in answer.recipes
        )
        structured_allergen_violations = audit_allergens(
            structured_text,
            _allergens_for_audit(),
            use_optional=True,
        )
        if structured_allergen_violations:
            structured_conditions = sorted({
                violation["condition"] for violation in structured_allergen_violations
            })
            merged_violations = sorted(
                set(state.get("verify_violated") or []) | set(structured_conditions)
            )
            # 与 allergen_block_node 同口径：只回纯文本的话右栏健康链是空的，
            # 用户只看到"菜没了"却看不到"为什么没了"。统一输出结构化 payload。
            block_tip = (
                "抱歉，这道菜在备料细节（调料或步骤）里含有需要避开的过敏原，"
                "我不能推荐。请告诉我家里现有的食材，我按不含该过敏原重新配一道。"
            )
            return {
                "messages": [AIMessage(content=json.dumps({
                    "opening": block_tip,
                    "recipes": [],
                    "image_url": None,
                    "image_ai_generated": False,
                    "image_requested": False,
                    "image_note": "",
                    "chef_tip": "",
                    "sources": [],
                    "health_lights": [],
                    "guardrails": [
                        item.model_dump()
                        for item in _build_guardrails(latest_text, "blocked", merged_violations)
                    ],
                    # UI 必须让用户看到「主菜当前面向谁」，否则家庭场景下无法判断该听谁的。
                    "primary_member": _active_member_name(),
                }, ensure_ascii=False))],
                "verify_status": "blocked",
                "verify_warning": "结构化卡片复核命中过敏原，已取消卡片输出。",
                "verify_violated": merged_violations,
            }
        # 声明式「约束纠正」轮：用户是在纠正成员/慢病的归属或有效性（「并没有说弟弟要减重，
        # 这个到后面都不需要管」），不是在改菜。这种情况下重跑结构化链只会让模型顺手把菜名
        # 换掉、再弹一张卡片，与用户意图完全相反，所以直接回纯文本、原卡片原地不动。
        # 位置是硬规则：产品形态短路必须排在结构化过敏原复核之后（见 tests/test_allergen_guardrail.py）。
        if _is_constraint_correction_turn(latest_text):
            return {"messages": []}
        # 两阶段点菜第一阶段（排在安全复核之后）：泛推荐轮只出候选清单，不出卡片。
        # 候选优先取正文里的编号菜名（与用户看到的编号一致），
        # 正文没按编号列时才退回结构化结果的菜名。
        if is_candidate_turn:
            candidate_names = _filter_candidate_names(
                _extract_candidate_names(_candidate_source_text(messages) or opening)
            )
            if len(candidate_names) < 2:
                candidate_names = _filter_candidate_names([
                    str(recipe.name).strip() for recipe in answer.recipes
                    if str(recipe.name).strip()
                ])
            candidate_payload = _candidate_list_payload(
                opening, candidate_names, latest_text, state
            )
            if candidate_payload is None:
                return {"messages": []}
            return {
                "messages": [
                    AIMessage(content=json.dumps(candidate_payload, ensure_ascii=False))
                ]
            }
        # 家庭差异化必须由 Python 规则确定性计算，模型自填矩阵只能作为候选，
        # 最终卡片统一用 build_matrix 的结果覆盖，保证可审计、可复现。
        answer = _apply_family_differentiation(answer)
        # 外出就餐场景：不清空 steps，改在 Prompt 里要求模型产出「取餐/怎么吃」而非烹饪步骤。#将实例中的菜谱进行排序
        # 健康护栏可见化：把 verify 的确定性审计结论注入卡片，供前端右栏渲染『健康链』
        answer.guardrails = _build_guardrails(
            latest_text,
            state.get("verify_status", ""),
            state.get("verify_violated", []),
            dish_matrix=answer.dish_matrix,
        )
        # 健康护栏：若多次重生成仍不通过，把安全警示带进卡片（绝不静默放行）
        warning = state.get("verify_warning", "")
        if warning:
            answer.chef_tip = (answer.chef_tip + " " + warning).strip()
        answer.image_requested = wants_images
        if not wants_images:
            for recipe in answer.recipes:
                recipe.image_url = None
                recipe.image_ai_generated = False
            answer.image_url = None
            answer.image_ai_generated = False
            answer.image_note = ""
        else:
            # 图片搜索已剥离到 chat_route 的后台线程（structure 曾在节点内串行搜图，
            # 四级图源瀑布+逐候选下载+视觉审核动辄 120s，是全链最大瓶颈）。
            # 这里只保留用户本轮上传图的兼容赋值；其余菜图由 SSE image 事件异步补推。
            if allow_multiple and answer.recipes:
                for index, recipe in enumerate(answer.recipes):
                    if not recipe.image_url and index == 0 and real_image_url:
                        recipe.image_url = real_image_url
                        recipe.image_ai_generated = real_image_ai
            elif answer.recipes and real_image_url:
                answer.recipes[0].image_url = real_image_url
                answer.recipes[0].image_ai_generated = real_image_ai
            # 代码兜底：图片 URL 以工具真实返回为准——有真链接才给图，没有就强制 null，
            # 杜绝"正文说找到图、卡片却没图"的口径不一
            if allow_multiple and answer.recipes:
                # 多道菜时顶层字段只保留第一道，供旧前端/旧历史兼容；新前端读取每道菜自己的 image_url。
                answer.image_url = answer.recipes[0].image_url
                answer.image_ai_generated = answer.recipes[0].image_ai_generated
            else:
                answer.image_url = answer.recipes[0].image_url if answer.recipes else None#赋值图片路径
                # 透明标注（项目亮点）：图片若由通义万相生成，强制让前端知道，绝不伪装成实拍图
                answer.image_ai_generated = (
                    answer.recipes[0].image_ai_generated if answer.recipes else False
                )#赋值图片是否由 AI 生成
            if answer.image_ai_generated:
                # 强制图注带「AI 生成示意图」，防止模型漏写导致误导用户/评委
                if not answer.image_note:#图片注为空
                    answer.image_note = "AI 生成示意图（非真实成品照，仅供样式参考）"
                elif "AI 生成示意图" not in answer.image_note:#图片注不是这个
                    answer.image_note = "AI 生成示意图：" + answer.image_note#给他加上
            elif answer.image_url is None:
                # 要图时先保持空注解，前端据此显示「AI 正在生成菜品图片...」占位；
                # 真正失败由 chat_route 的 image_failed 事件统一落失败文案。
                answer.image_note = answer.image_note or ""
        # primary_member 刻意不进 ChefAnswer schema：不改模型格式指令，避免扰动结构化解析；
        # 只在出卡时由代码注入，语义是「本轮主菜按谁的健康约束求解」。
        payload = {
            "opening": opening,
            "primary_member": _active_member_name(),
            **answer.model_dump(),
        }
        return {
            "messages": [
                AIMessage(content=json.dumps(payload, ensure_ascii=False))#ai结构化完的消息加进 messages
            ]
        }
    except Exception:#降级：不追加任何消息，前端按旧 markdown 渲染 opening
        return {"messages": []}#不变成节构卡片了

# --------------------------------------------------------------------------- #
#  4.5 健康护栏节点（L3 硬护栏）：chef_think 输出后、结构化前做确定性审计
# --------------------------------------------------------------------------- #
MAX_VERIFY = 3  # 打回重生成的上限，防无限循环

# 自定义状态：在 MessagesState 基础上扩展护栏所需的计数字段
class ChefState(MessagesState):
    verify_attempts: int      # 已打回重生成次数
    verify_warning: str       # 超限仍不通过时带给前端的安全警示
    verify_status: str        # ok / retry / degraded / blocked，供条件边路由
    verify_violated: list = []  # 本轮曾命中的病种列表（供右栏『健康链』如实展示）
    profile_ready: bool = True  # 充分性门控：健康画像是否足够进入检索/审计（AgentMental 范式）
    profile_missing: list = []  # 充分性门控：本轮尚未确认的高风险病种
    tool_calls_in_turn: int = 0  # 本轮已执行工具调用数，入口重置
    tool_budget_exhausted: bool = False  # 本轮工具预算是否耗尽，供日志/降级识别
    tool_budget: int = 0  # 本轮工具预算上限（选定轮收紧为 2，见 _turn_tool_budget）

@trace_node("verify_answer")
def verify_answer_node(state: ChefState):
    """输出前硬审计：慢病可降级，过敏原连续命中时必须阻断，不得放行。"""
    attempts = state.get("verify_attempts", 0)
    previous_violations = list(state.get("verify_violated") or [])
    # 取最新一条 chef_think 的自然语言回答做审计
    answer_text = ""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            answer_text = str(m.content)
            break
    user_text = _latest_user_text(state["messages"])
    conditions = _merged_conditions(user_text)
    allergens = _allergens_for_audit()
    is_candidate_turn = _is_candidate_turn(state["messages"])
    candidate_names = _extract_candidate_names(answer_text) if is_candidate_turn else []
    # 候选轮的安全说明常会原样提到“虾/花生”等禁忌词来提醒用户，不能把说明本身
    # 当成菜品违规，否则会触发无谓重生成。真正需要硬审计的是用户看到的候选菜名。
    audit_text = "\n".join(candidate_names) if candidate_names else answer_text
    allergen_violations = audit_allergens(
        audit_text,
        allergens,
        use_optional=True,
    )
    violations = audit(audit_text, conditions) + allergen_violations
    has_allergen_violation = any(
        str(v.get("condition", "")).startswith("过敏原:")
        for v in violations
    )
    violated_conditions = sorted({v["condition"] for v in violations})
    if not violations:
        notices = audit_allergen_advisories(
            answer_text,
            allergens,
            use_optional=True,
        )
        notice_warning = ""
        if notices:
            notice_warning = "⚠️ 过敏原提示：" + "；".join(
                f"{v['condition']}需确认「{v['keyword']}」是否含致敏成分"
                for v in notices
            )
        return {"verify_status": "ok", "verify_attempts": attempts + 1,
                "verify_warning": notice_warning,
                "verify_violated": previous_violations}
    # 工具预算已经耗尽时，过敏原不能再打回空转，直接阻断并给出确定性替代。
    if has_allergen_violation and state.get("tool_budget_exhausted"):
        return {"verify_status": "blocked", "verify_warning": "",
                "verify_violated": sorted(
                    set(previous_violations) | set(violated_conditions)
                )}
    # 过敏原达到重试上限后必须 blocked；慢病仍保持既有 degraded 语义。
    if has_allergen_violation and attempts >= MAX_VERIFY:
        return {"verify_status": "blocked", "verify_warning": "",
                "verify_violated": sorted(
                    set(previous_violations) | set(violated_conditions)
                )}
    if state.get("tool_budget_exhausted"):
        warn = "健康护栏提示：工具预算已耗尽，本轮先按已有信息保守收口；仍需注意——" + "；".join(
            f"{v['condition']}忌{v['keyword']}" for v in violations
        )
        return {"verify_status": "degraded", "verify_warning": warn,
                "verify_violated": sorted(set(previous_violations) | set(violated_conditions))}
    if attempts < MAX_VERIFY:
        feedback_text = describe(violations)
        if has_allergen_violation:
            feedback_text += (
                "\n过敏原是绝对硬约束，必须完全避开，包括调料与隐含来源"
                "（如酱油含小麦、蛋黄酱含蛋）；请换一道完全不含该成分的菜。"
            )
        retry_instruction = (
            "请保持候选清单格式：只输出编号候选菜名和简短理由，替换违规候选，"
            "不要输出完整做法、步骤、调料或卡片。"
            if is_candidate_turn
            else "请换用符合该人群膳食原则的食材与调料，保持菜谱可执行，只输出一道菜。"
        )
        feedback = HumanMessage(content=(
            "[健康护栏审核] 你给出的方案违反了以下硬禁忌，必须重新生成一道合规的菜：\n"
            + feedback_text
            + "\n" + retry_instruction
        ))
        return {"verify_status": "retry", "verify_attempts": attempts + 1,
                "verify_violated": sorted(set(previous_violations) | set(violated_conditions)),
                "messages": [feedback]}
    # 已达上限仍不通过：放行但附安全警示，绝不静默放行
    warn = "⚠️ 健康护栏提示：本方案经多次重生成仍含需注意项——" + "；".join(
        f"{v['condition']}忌{v['keyword']}" for v in violations
    )
    return {"verify_status": "degraded", "verify_warning": warn,
            "verify_violated": sorted(set(previous_violations) | set(violated_conditions))}


@trace_node("allergen_block")
def allergen_block_node(state: ChefState):
    """过敏原连续命中：不调 LLM、不输出违规卡片，只给确定性安全替代方向。"""
    labels = []
    for condition in state.get("verify_violated") or []:
        text = str(condition)
        if text.startswith("过敏原:"):
            label = text.split(":", 1)[1].strip()
            if label and label not in labels:
                labels.append(label)
    safe = suggest_safe_dishes(
        _allergens_for_audit(),
        k=3,
        use_optional=True,
    )
    subject = "、".join(labels) if labels else "相关过敏原"
    tip = (
        f"抱歉，在{subject}过敏的前提下，这道菜我不能推荐。"
        "以下是不含该成分的替代方向："
        + "、".join(safe)
        + "。请挑一个，我再给完整做法。"
    )
    if len(safe) < 3:
        tip += (
            "当前成员的过敏原限制下，安全可共用的家常菜较少；"
            "其他无该过敏原的成员仍可按原菜就餐，相关成员单独替换并避免交叉接触。"
        )
    violated = state.get("verify_violated") or []
    guardrails = [
        item.model_dump()
        for item in _build_guardrails(
            _latest_user_text(state.get("messages", [])), "blocked", violated
        )
    ]
    # 仍然输出结构化 payload（recipes 为空数组）：
    # 前端只在拿到 ChefAnswer 结构时才渲染右栏『健康链』，若这里只回纯文本，
    # 就会变成"拦是拦住了，但没有任何可见证据"——答辩时拿不出来。
    # recipes 给空数组：App.tsx 用 Array.isArray(recipes) 判定，空数组不会渲染卡片。
    payload = {
        "opening": tip,
        "recipes": [],
        "image_url": None,
        "image_ai_generated": False,
        "image_requested": False,
        "image_note": "",
        "chef_tip": "",
        "sources": [],
        "health_lights": [],
        "guardrails": guardrails,
    }
    return {
        "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
        "verify_warning": tip,
        "verify_status": "blocked",
    }


@trace_node("profile_memory")
def profile_memory_node(state: ChefState):
    """结构化回答后提取画像候选；默认关闭时不产生任何状态变化。"""
    if not PROFILE_MEMORY_ENABLED:
        return {}
    text = _latest_user_text(state.get("messages", []))
    if not text:
        return {}
    try:
        from memory_candidates import extract_candidates, remember_candidates

        family = {}
        path = Path(__file__).resolve().parent / "data" / "profile.json"
        if path.exists():
            family = json.loads(path.read_text(encoding="utf-8"))
        candidates = extract_candidates(text, family.get("members") or [])
        candidates = remember_candidates(candidates)
    except Exception:
        return {}
    if not candidates:
        return {}
    candidate = candidates[0]
    member = candidate.get("member") or "这位家人"
    value = candidate.get("value") or "这条饮食限制"
    message = (
        f"我记下了：{member}{value}。要加入{member}的长期饮食画像吗？"
        "你可以确认、仅本次记住或永久不记录。"
    )
    return {"messages": [AIMessage(content=message)]}


def verify_route(state: ChefState) -> str:
    """根据审计状态路由：ok/degraded → 结构化收尾；retry → 回到思考节点。
    P3 性能二段：仅当用户明确是非菜品查询（闲聊/纯健康问答）时才直通 END，
    跳过 structure LLM —— 任何可能输出菜品卡片（含图片）的轮次都保留结构化流程。"""
    status = state.get("verify_status", "ok")
    # 安全相关的两个终态优先返回，不被下面的追问短路影响（retry 必须回炉重生成）
    if status in ("blocked", "retry"):
        return status
    if state.get("tool_budget_exhausted") and not _is_dish_pick_turn(
        state.get("messages", [])
    ):
        # 预算耗尽就收口，是为了避免"继续空转"；但如果本轮用户已经明确选定了一道菜，
        # 直接走纯文本会让卡片和配图一起消失（实测「2吧…父亲高血压，可以吃这个吗」）。
        # 选定轮保留结构化出卡：结构化链仍在卡片输出前做过敏原复核，
        # 且 verify_answer_node 对 blocked + 预算耗尽的硬阻断不受影响。
        return "plain"
    # 追问轮次直接收口：整段以问句收尾且没有菜谱结构时，不进结构化。
    # 否则「先问用户想法」的那一轮仍会附带一张卡片，与追问语义自相矛盾。
    last_ai = ""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            last_ai = str(m.content).strip()
            break
    if _is_ask_turn(last_ai):
        return "plain"
    if status == "ok":
        has_tool_result = any(
            isinstance(m, ToolMessage) for m in state.get("messages", [])[-8:]
        )
        if not has_tool_result:
            txt = _latest_user_text(state.get("messages", []))
            if txt and not _is_dining_scene(txt) and "菜" not in txt and "做" not in txt and "吃" not in txt:
                return "plain"
    return status


def _is_followup_only(text: str) -> bool:
    """健康问答追问态：整段以问句收尾且没有菜谱结构时才跳过出菜。"""
    text = str(text or "").strip()
    if not text or not text.endswith(("？", "?")):
        return False
    recipe_markers = (
        "食材", "做法", "步骤", "调料", "菜谱", "配料", "食用油",
        "盐", "克", "毫升", "分钟", "翻炒", "蒸", "煮", "炸", "烤",
        "食材清单", "用量", "第一道", "第二道",
    )
    return not any(marker in text for marker in recipe_markers)


# 带征询语气的收尾问句（「你想吃清淡的还是重口的？」）
_ASK_TURN_MARKERS = (
    "吗", "呢", "还是", "要不要", "哪个", "哪道", "哪一", "是不是",
    "确认一下", "告诉我", "请说", "你希望",
)
# 出现 ≥2 个强菜谱结构词，说明这段是在出菜而不只是提问（不能因为结尾问句就丢掉卡片）
_STRONG_RECIPE_MARKERS = ("步骤", "第一道", "食材清单", "调料表", "用量", "克", "毫升", "分钟")


def _looks_like_ask_turn(text: str) -> bool:
    """整段是一条追问（而非出菜）：以问句收尾、带征询语气、且没有成形的菜谱结构。"""
    raw = str(text or "").strip()
    if not raw or len(raw) > 200:
        return False
    if not raw.endswith(("？", "?")):
        return False
    if not any(marker in raw for marker in _ASK_TURN_MARKERS):
        return False
    if sum(1 for marker in _STRONG_RECIPE_MARKERS if marker in raw) >= 2:
        return False
    return True


def _is_ask_turn(text: str) -> bool:
    """追问轮次判定：先复用已有的健康问答追问口径，再补一层征询语气判定。"""
    return _is_followup_only(text) or _looks_like_ask_turn(text)


# 「约束纠正」轮的词汇锚点：成员称呼 / 慢病与目标 / 否定纠正语气。
_CORRECTION_MEMBER_WORDS = (
    "爸爸", "父亲", "妈妈", "母亲", "老婆", "妻子", "老公", "丈夫", "儿子", "女儿",
    "弟弟", "哥哥", "姐姐", "妹妹", "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "家人", "全家", "孩子", "老人",
)
_CORRECTION_CONDITION_WORDS = (
    "高血压", "血压", "糖尿病", "血糖", "减重", "减肥", "超重", "肥胖", "体重",
    "痛风", "高尿酸", "高血脂", "高脂血症", "血脂", "肾病", "慢性肾脏病",
    "过敏", "忌口", "孕期",
)
_CORRECTION_NEGATION_WORDS = (
    "并没有", "没有说", "没说", "不需要", "不用", "不必", "别管", "忽略",
    "无关", "搞错", "弄错", "记错", "不是",
)
# 出现这些就说明用户是在「动这道菜」（换菜 / 没胃口），不能再当纠正轮吞掉
_CORRECTION_CHANGE_WORDS = (
    "换一道", "换一个", "换别的", "换成", "改成", "做成", "换做", "改做", "改为",
    "变成", "没胃口", "不想吃", "吃不下", "胃口", "食欲",
)


def _is_constraint_correction_turn(text: str) -> bool:
    """声明式「约束纠正」轮：用户在纠正成员/慢病的归属或有效性，而不是在改菜。

    典型说法：「并没有说弟弟要减重，这个到后面都不需要管」。这类话如果放行到结构化链，
    模型会顺手把菜名换掉、再弹一张卡片（实测「柠檬酸香清蒸鲈鱼」被改成「柠檬米醋蒸鲈鱼」），
    与用户意图完全相反。

    判定必须确定性，且要求「既命中纠正语气、又确认没有在动这道菜」：
      1. 命中否定/纠正语气词；
      2. 命中成员称呼或慢病/目标词（说明话题是「谁有什么约束」，不是「菜怎么做」）；
      3. 不含任何菜品调整词、换菜词、没胃口类词，也没有点名具体菜；
      4. 不是问句（问句走 _is_ask_turn 那条路，由它决定是否保留正文）。
    这条判定只用来「不出新卡片」，不改动任何安全审计结论；代价是纠正轮仍会跑一次
    结构化链（因为它必须排在过敏原复核之后），但结果被丢弃、不会呈现给用户。
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 80:
        return False
    if raw.endswith(("？", "?")):
        return False
    if not any(word in raw for word in _CORRECTION_NEGATION_WORDS):
        return False
    if not (
        any(word in raw for word in _CORRECTION_MEMBER_WORDS)
        or any(word in raw for word in _CORRECTION_CONDITION_WORDS)
    ):
        return False
    # 先把纠正语气词本身洗掉再查调整词，否则「别管这条」会被「别」误判成在调味道
    scrubbed = raw
    for word in _CORRECTION_NEGATION_WORDS:
        scrubbed = scrubbed.replace(word, "")
    # 宁可多出一张卡，也不吞掉真实的改菜意图
    if any(word in scrubbed for word in _DISH_ADJUST_WORDS):
        return False
    if any(word in scrubbed for word in _CORRECTION_CHANGE_WORDS):
        return False
    return True

# --------------------------------------------------------------------------- #
#  4.5 充分性门控（AgentMental 范式）：高风险健康决策前先确定性判断画像是否足够
# --------------------------------------------------------------------------- #
def _parse_declared_conditions(user_text: str) -> set:
    """解析画像中的已声明病种，兼容旧字段和当前渲染格式。"""
    import re
    segments = []
    for pattern in (
        r"慢病约束\s*=\s*([^】\n]*)",
        r"慢病情况\s*[：:]\s*([^\n（(]*)",
        r"当前目标\s*[：:]\s*([^\n（(]*)",
    ):
        segments.extend(match.group(1) for match in re.finditer(pattern, user_text))
    declared = set()
    for segment in segments:
        segment = segment.strip()
        if segment in ("无", "无特殊", ""):
            continue
        declared.update(
            item.strip()
            for item in re.split(r"[、,，;；\s]+", segment)
            if item.strip()
        )
    return declared

def _declared_covers(condition: str, declared: set) -> bool:
    """模糊匹配：declared 任一包含/被包含于 condition 即视为已声明该约束。"""
    return any(condition in d or d in condition for d in declared)


def _hit_without_negation(text: str, phrase: str) -> bool:
    """子串命中且命中点前两字内无否定词（『没有高血压』不算声明高血压）。"""
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx < 0:
            return False
        if not any(neg in text[max(0, idx - 2) : idx] for neg in ("没", "无", "非")):
            return True
        start = idx + 1


def _self_declared_conditions(user_text: str) -> set:
    """解析用户本轮原话中明确自述的健康状态，避免重复追问。

    放宽版：子串别名匹配（『妊娠期高血压』『孕18周』『尿酸偏高』等自然措辞
    都算声明），并排除否定前缀——『我没有高血压』不会被误认成声明。"""
    text = (user_text or "").replace(" ", "")
    out = set()
    pairs = [
        ("高血压", ("高血压", "血压高", "血压偏高", "血压不稳")),
        ("糖尿病", ("糖尿病", "血糖高", "血糖偏高", "血糖不稳")),
        ("高脂血症", ("高血脂", "高脂血症", "血脂高", "血脂偏高", "甘油三酯")),
        ("痛风", ("痛风", "尿酸高", "尿酸偏高", "高嘌呤")),
        ("慢性肾脏病", ("肾病", "肾脏不好", "肾功能", "肾炎", "肌酐高")),
        ("肥胖", ("肥胖", "体重超标", "超重", "减肥", "减重", "控制体重")),
        ("孕期", ("怀孕", "孕妇", "孕期", "妊娠", "孕")),
    ]
    for condition, phrases in pairs:
        if condition == "孕期" and "备孕" in text:
            continue  # 备孕≠孕期，营养建议差异大，宁可追问
        if any(_hit_without_negation(text, phrase) for phrase in phrases):
            out.add(condition)
    return out


@trace_node("profile_gate")
def profile_gate_node(state: ChefState):
    """充分性门控节点：进入 chef_think 前，确定性判断健康画像是否足够。

    - 信息足够：profile_ready=True，放行到 chef_think；
    - 信息不足：profile_ready=False，路由到 ask_user，不进入工具检索/审计。
    """

    messages = state["messages"]
    latest_user_idx = _latest_user_index(messages)
    recent_messages = messages[latest_user_idx:] if latest_user_idx is not None else messages

    # 只检查最近一条用户消息之后是否已经触发过门控，避免历史门控影响后续轮次。
    if any(
        isinstance(m, SystemMessage)
        and "充分性门控·必须追问" in str(m.content)
        for m in recent_messages
    ):
        return {"profile_ready": True, "profile_missing": []}

    user_text = _latest_user_text(messages)
    conditions = _merged_conditions(user_text)
    if not conditions:
        return {"profile_ready": True, "profile_missing": []}

    declared = (
        _parse_declared_conditions(user_text)
        | _self_declared_conditions(user_text)
        | set(_active_profile_conditions())
    )
    missing = [c for c in conditions if not _declared_covers(c, declared)]
    if not missing:
        return {"profile_ready": True, "profile_missing": []}

    return {"profile_ready": False, "profile_missing": missing}


@trace_node("ask_user")
def ask_user_node(state: ChefState):
    """生成一条面向用户的追问，不调用工具、不生成菜谱。"""

    missing = list(state.get("profile_missing") or [])
    if not missing:
        return {
            "messages": [
                AIMessage(content="请补充一下你的健康情况，我才能给出更安全的饮食建议。")
            ]
        }

    user_text = _latest_user_text(state["messages"])
    try:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "你是小膳管家。当前只负责向用户提出一个简短澄清问题。"
                        "禁止给出菜谱、禁止调用工具、禁止使用工具调用。"
                        "只追问用户健康状态或忌口严格程度，1-2 句话，语气自然。"
                    )
                ),
                HumanMessage(
                    content=(
                        "用户本轮说："
                        + user_text
                        + "\n\n尚未确认的高风险健康约束："
                        + "、".join(missing)
                    )
                ),
            ]
        )
        content = str(response.content).strip()
    except Exception:
        content = ""

    if not content:
        content = "想确认一下，你是否涉及" + "、".join(missing) + "？请简单告诉我你的饮食控制情况。"

    return {"messages": [AIMessage(content=content)]}


def profile_gate_route(state: ChefState) -> str:
    """信息足够走主流程，信息不足走追问节点。"""

    return "ask" if not state.get("profile_ready", True) else "ready"
# --------------------------------------------------------------------------- #
#  5. 构建图实例（状态图 + 条件边 + 回边 = LangGraph 循环 Agent）
# --------------------------------------------------------------------------- #
# 创建LangGraph图状态机，通过节点流转规则读写、操控全局上下文容器MessagesState
workflow = StateGraph(ChefState)#创建状态图，状态容器扩展为带护栏字段的 ChefState
# 注册两个节点，左是节点名，右是函数
workflow.add_node("chef_think", chef_agent_node)#思考节点自己思考能看图片
workflow.add_node("run_tools", run_tools_node)#工具执行节点
workflow.add_node("condense_history", maybe_condense)# 注册长对话压缩节点
workflow.add_node("verify_answer", verify_answer_node)# 注册健康护栏审计节点
workflow.add_node("structure_answer", structure_answer_node)# 注册结构化回答节点
workflow.add_node("profile_memory", profile_memory_node)# 画像候选后置节点（默认关闭）
workflow.add_node("allergen_block", allergen_block_node)# 过敏原硬拦截：确定性安全文案，不调用 LLM
workflow.add_node("profile_gate", profile_gate_node)# 注册充分性门控节点（AgentMental 范式）
workflow.add_node("ask_user", ask_user_node)# 注册健康画像追问节点
workflow.add_node("tool_budget_finalize", tool_budget_finalize_node)# 工具预算耗尽后的优雅收尾节点
workflow.set_entry_point("condense_history")# 设置入口：先压缩历史
workflow.add_edge("condense_history", "profile_gate")#连线长对话压缩→充分性门控
workflow.add_conditional_edges(
    "profile_gate",
    profile_gate_route,
    {
        "ready": "chef_think",
        "ask": "ask_user",
    },
)
workflow.add_edge("ask_user", END)#追问结束后本轮结束，等待用户下一轮回答

# 核心循环逻辑：条件边
# 1. LLM 思考完，判断是否要调用工具：要调用→去执行工具；不调用→去健康护栏审计
workflow.add_conditional_edges(#条件分支边
    source="chef_think",#源节点
    path=chef_route_with_tool_budget,# 有工具调用时先过本轮预算
    path_map={#条件分支二选一对应目标节点
        "tools": "run_tools",#有就执行工具
        "verify": "verify_answer",#不调用工具→先过健康护栏审计，再结构化收尾
        "tool_budget_exhausted": "tool_budget_finalize",#工具超预算→普通结论收口
    },
)
workflow.add_edge("tool_budget_finalize", "verify_answer")

# 2. 工具执行完毕，**回流到 LLM 节点再次思考（实现循环！）**
#    这就是 create_agent 做不到的闭环：工具结果回来重新让 LLM 校验、二次搜索、反思修
workflow.add_edge("run_tools", "chef_think")#再连线将工具执行结果回溯到思考节点

# 2.5 健康护栏审计后的路由：
#   ok / degraded → 结构化收尾；retry → 打回 chef_think 重新生成（带次数上限）
workflow.add_conditional_edges(#新增一个条件边分支
    source="verify_answer",
    path=verify_route,
    path_map={"ok": "structure_answer", #下一步进入结构化输出
              "retry": "chef_think",#回去重新生成
              "degraded": "structure_answer",#能用就节构化输出
              "blocked": "allergen_block",#过敏原不得降级放行
              "plain": END},#P3：无工具纯问答直接结束，跳过结构化 LLM
)
# 3. 结构化回答完毕 → 可选画像候选提取；关闭时节点直接透传后结束
workflow.add_edge("structure_answer", "profile_memory")
workflow.add_edge("profile_memory", END)
# 过敏原阻断文案输出后结束，不得再进入结构化节点生成违规卡片
workflow.add_edge("allergen_block", END)
# 编译可运行的图，挂载 Sqlite 断点持久化
agent = workflow.compile(checkpointer=checkpointer)#创建可运行的图的实例对象
