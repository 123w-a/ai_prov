# agent_graph.py：组装 LangGraph 循环 Agent（模型/断点/节点/状态图）
# 只负责"编排"：把提示词、工具、LLM、记忆、路由串成一张可运行的图
# 具体工具怎么干活看 agent_tools.py，提示词内容看 agent_prompts.py

import json#结构化回答打包成 JSON 字符串落进消息
import openai#捕获上游 LLM 偶发 5xx/超时异常做重试
import time#重试间隔用
import sqlite3#持久化短期记忆（断点续跑、循环状态保存）
from model_name import get_langchain_llm, resolve_provider#一个创造模型，一个说明用的是哪个模型
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

from agent_prompts import SYSTEM_PROMPT#最上层的提示词从这里输出ai的最先回复
from agent_tools import find_recipe_image, set_query_transform_llm, tools, web_search
from agent_chains import build_structured_answer, rank_recipes#LCEL 结构化链(prompt|llm|parser)+排序+格式自动重试
from agent_schemas import GuardrailItem  # 健康护栏审计结论（运行时注入 ChefAnswer.guardrails）
#build_structured_answer标准链+parser检查出错误后再进行重试
from nutrition_rules import detect_conditions, audit, describe, RULES  # L3 硬护栏：确定性健康禁忌审计

# 稳定输出规则：覆盖旧提示词中的多菜分支，保证流式正文和结构化卡片一致。
SINGLE_RECIPE_RULE = (
    "\n\n【默认单菜规则】用户没有明确要求多个选择时，"
    "每次最终只回答最合适的一道菜，不要列出第二道、备选菜或并列方案。"
)
# --------------------------------------------------------------------------- #
#  1. 模型 & 工具绑定
# --------------------------------------------------------------------------- #
# 主脑运行模型：不传参即走"自适配"——优先读 .env 的 CHEF_PROVIDER 开关（想用哪个写哪个），
# 没配 / 配了但没 key → 自动用 configs 第一个可用的（已将 gpt 放第一，不写即默认 gpt）。
# 本文件不硬编码任何模型名，切换模型只改 .env，无需动代码。
provider = resolve_provider()#不写默认是.env中设置的第一个key
llm = get_langchain_llm(provider)#获取模型对象

# 检索侧思考（查询改写 / 多查询 / HyDE）用便宜的 deepseek，不占用贵的主模型额度。
# 检索是高频、低难度任务，deepseek 足够且成本远低于 gpt，默认即走 deepseek。
try:
    retrieval_llm = get_langchain_llm("deepseek", temperature=0.3, max_tokens=200)
except Exception:
    retrieval_llm = llm

def _query_transform_adapter(system: str, user: str) -> str:
    try:
        return retrieval_llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        ).content
    except Exception:
        return ""
set_query_transform_llm(_query_transform_adapter, mode="multi")

# 历史摘要专用模型：用廉价 deepseek 做长对话压缩（省成本），无 key 时自动回退主模型
try:
    summary_llm = get_langchain_llm("deepseek", temperature=0.3)
except Exception:
    summary_llm = llm


llm_with_tools = llm.bind_tools(tools)#传个大模型告诉他有什么工具和怎么正确的用变成json格式给LLM
#让大模型知到要怎么去指挥工具
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
#MessagesState是所有的状态消息，包含 messages 属性
def maybe_condense(state: MessagesState):#压缩历史对话
    msgs = state["messages"]
    # 最后一条消息不是用户发的或者没有消息的话就不执行后续操作
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {}#不改变状态，防止打断正在运行的时候
    # 只统计 user + ai 对话，跳开 ToolMessage 工具返回，避免把冗长搜索结果搅进摘要
    talk = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]#自动过滤没用消息
    if len(talk) <= MAX_HISTORY_KEEP:
        return {}  # 还没到阈值，不压缩不做处理
    old = talk[:-MAX_HISTORY_KEEP]#从开始到倒数第max_history_keep，老的对话要总结后删除
    if not old:
        return {}
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
            summary = summary_llm.invoke([HumanMessage(content=summary_prompt)]).content#获取摘要（用廉价 deepseek）
            break#服务器错误，网络错误，调用次数超过限制
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError):
            time.sleep(1 + attempt)
    if not summary:
        return {}#摘要生成失败就跳过没有增量
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
    return {"messages": removals + [summary_msg]}#返回给langgraph处理，看成是先删后加
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


def _current_request_text(text):
    """从用户消息中取出本次需求，排除每轮自动注入的长期偏好。"""
    marker = "【以上为偏好约束，以下是本次需求】"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip()


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


def chef_agent_node(state: MessagesState):
    messages = state["messages"]#已经被压缩过后的4种消息类的消息
    # 前置插入系统提示词，再追加历史对话消息（先清掉孤儿 ToolMessage 防 API 400）
    latest_text = _latest_user_text(messages)
    latest_has_image = _latest_user_has_image(messages)
    is_new_image_request = (
        latest_has_image and _is_new_ingredient_image_request(latest_text)
    )
    prompt_content = SYSTEM_PROMPT
    if not _wants_multiple_recipes(messages):
        prompt_content += SINGLE_RECIPE_RULE
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
                parts.append(
                    "本轮 Agent 已确认的回答（菜名和食材以此为准）：\n"
                    + ai_text
                )
            break
    # 本轮所有 web_search 工具返回（JSON 字符串：text 搜索结果 + image_url 图片 + image_source 图源）
    search_blocks = []#2.有连坐删除，删的时候会把工具的返回结果也会删除不搞混
    for m in context_messages:#把工具返回的搜索结果都塞进parts不搞混，1.只要最后3条并且是从最晚覆盖最早这样找的
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
        parts.append(
            "搜索结果（每条是 JSON：text 为搜索文本、image_url 为成品图链接或 null、"
            "image_source 为图源 real/ai）：\n"
            + "\n\n".join(search_blocks[-3:])#最多取最近3次搜索，
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


def _latest_user_text(messages):
    """提取本轮用户的文字需求，图文消息只取文字部分。"""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要"):
            return _current_request_text(_message_text(m))
    return ""


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
        image_url, image_source = find_recipe_image(recipe_name)
        return image_url, image_source == "ai"
    except Exception:
        return None, False


def _build_guardrails(user_text, verify_status, verify_violated):
    """依据 verify 节点真实审计结论，为前端右栏拼出『本轮健康护栏』列表（健康链可见化的核心）。

    - 对每个检测到的健康标签，给出 pass / warn / adjusted 结论与一句理由；
    - 不依赖 LLM，以 verify 的确定性审计口径为准，避免关键字子串误判
      （如『少盐』被『盐』误命中导致合规方案被误标为已调整）；
    - 与 verify_answer_node 共用同一套 RULES，口径一致。
    """
    conditions = detect_conditions(user_text)
    if not conditions:
        return []
    violated = set(verify_violated or [])
    items = []
    for cond in conditions:
        rule_msg = RULES.get(cond, {}).get("message", "")
        if cond in violated and verify_status == "degraded":
            status, reason = "warn", "经多次重生成仍有需注意项，请谨慎：" + rule_msg
        elif cond in violated:
            # 曾被硬护栏命中、但最终通过了审计（已重生成至合规）
            status, reason = "adjusted", "初始方案命中硬禁忌，已由健康护栏自动调整至合规：" + rule_msg
        else:
            status, reason = "pass", "已符合" + cond + "膳食原则"
        items.append(GuardrailItem(condition=cond, rule=rule_msg, status=status, reason=reason))
    return items


def structure_answer_node(state: MessagesState):#结构化回答节点
    messages = state["messages"]
    # chef_think 最后一轮的自然语言回答 = 流式已经推给前端的正文，原样保留进 opening
    opening = ""
    if messages and isinstance(messages[-1], AIMessage):
        opening = str(messages[-1].content)#这里已经传入前端了，所以比结构化卡片快
        #context是纯文本给了LCEL节构化链，其他的图片的链接和图片的来源在下文传出来的answer来赋值
    latest_text = _latest_user_text(messages)
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
    try:#结构化链带「格式自动重试」：解析失败会回灌 LLM 修正，重试耗尽才降级
        answer = build_structured_answer(context)#会返回一个实例
        answer = rank_recipes(answer, allow_multiple=allow_multiple)#将实例中的菜谱进行排序
        # 健康护栏可见化：把 verify 的确定性审计结论注入卡片，供前端右栏渲染『健康链』
        answer.guardrails = _build_guardrails(
            latest_text,
            state.get("verify_status", ""),
            state.get("verify_violated", []),
        )
        # 健康护栏：若多次重生成仍不通过，把安全警示带进卡片（绝不静默放行）
        warning = state.get("verify_warning", "")
        if warning:
            answer.chef_tip = (answer.chef_tip + " " + warning).strip()
        if allow_multiple:#recipe是单个食谱对象
            for index, recipe in enumerate(answer.recipes):
                recipe.image_url, recipe.image_ai_generated = _search_recipe_image(recipe.name)
                # 第一道菜兼容本轮已经拿到的图片，避免重复搜索失败时整轮无图。
                if not recipe.image_url and index == 0:
                    recipe.image_url = real_image_url#这里就直接把图片赋值给他结构化的时候就会变成图片
                    recipe.image_ai_generated = real_image_ai#看是否有AI标
        elif answer.recipes:
            # 单道菜模式按最终菜名重新搜索，避免把泛食材搜索结果误绑到菜品卡片。
            answer.recipes[0].image_url, answer.recipes[0].image_ai_generated = (
                _search_recipe_image(answer.recipes[0].name)
            )
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
        elif answer.image_url is None and not answer.image_note:#图片注为空并且没有图片
            answer.image_note = "未找到可正常展示的成品图片。"
        payload = {"opening": opening, **answer.model_dump()}
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
    verify_status: str        # ok / retry / degraded，供条件边路由
    verify_violated: list = []  # 本轮被硬护栏命中的病种列表（供右栏『健康链』如实展示）
    profile_ready: bool = True  # 充分性门控：健康画像是否足够进入检索/审计（AgentMental 范式）
    profile_missing: list = []  # 充分性门控：本轮尚未确认的高风险病种

def verify_answer_node(state: ChefState):
    """输出前硬审计：发现硬禁忌则带反馈打回 chef_think 重生成（最多 MAX_VERIFY 次）。"""
    attempts = state.get("verify_attempts", 0)
    # 取最新一条 chef_think 的自然语言回答做审计
    answer_text = ""
    for m in reversed(state["messages"]):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            answer_text = str(m.content)
            break
    user_text = _latest_user_text(state["messages"])
    conditions = detect_conditions(user_text)
    violations = audit(answer_text, conditions)
    violated_conditions = sorted({v["condition"] for v in violations})
    if not violations:
        return {"verify_status": "ok", "verify_attempts": attempts + 1,
                "verify_warning": "", "verify_violated": []}
    if attempts < MAX_VERIFY:
        feedback = HumanMessage(content=(
            "[健康护栏审核] 你给出的方案违反了以下硬禁忌，必须重新生成一道合规的菜：\n"
            + describe(violations)
            + "\n请换用符合该人群膳食原则的食材与调料，保持菜谱可执行，只输出一道菜。"
        ))
        return {"verify_status": "retry", "verify_attempts": attempts + 1,
                "verify_violated": violated_conditions, "messages": [feedback]}
    # 已达上限仍不通过：放行但附安全警示，绝不静默放行
    warn = "⚠️ 健康护栏提示：本方案经多次重生成仍含需注意项——" + "；".join(
        f"{v['condition']}忌{v['keyword']}" for v in violations
    )
    return {"verify_status": "degraded", "verify_warning": warn,
            "verify_violated": violated_conditions}

def verify_route(state: ChefState) -> str:
    """根据审计状态路由：ok/degraded → 结构化收尾；retry → 回到思考节点。"""
    return state.get("verify_status", "ok")#创键的类中有这个字符，兜底是ok进入结果化输出

# --------------------------------------------------------------------------- #
#  4.5 充分性门控（AgentMental 范式）：高风险健康决策前先确定性判断画像是否足够
# --------------------------------------------------------------------------- #
def _parse_declared_conditions(user_text: str) -> set:
    """从注入的健康画像前缀【健康画像：慢病约束=高血压、糖尿病】解析已声明病种。"""
    import re
    m = re.search(r"慢病约束\s*=\s*([^】\n]*)", user_text)
    if not m:
        return set()
    seg = m.group(1).strip()
    if seg in ("无", "无特殊", ""):
        return set()
    return {i.strip() for i in re.split(r"[、,，;；\s]+", seg) if i.strip()}

def _declared_covers(condition: str, declared: set) -> bool:
    """模糊匹配：declared 任一包含/被包含于 condition 即视为已声明该约束。"""
    return any(condition in d or d in condition for d in declared)


def _self_declared_conditions(user_text: str) -> set:
    """解析用户本轮原话中明确自述的健康状态，避免重复追问。"""

    text = (user_text or "").replace(" ", "")
    out = set()
    pairs = [
        ("高血压", ("我有高血压", "我患有高血压", "我血压高", "我是高血压")),
        ("糖尿病", ("我有糖尿病", "我患有糖尿病", "我糖尿病", "我是糖尿病")),
        ("高脂血症", ("我有高血脂", "我有高脂血症", "我血脂高")),
        ("痛风", ("我有痛风", "我痛风", "我尿酸高")),
        ("慢性肾脏病", ("我有肾病", "我有慢性肾脏病", "我肾脏不好")),
        ("肥胖", ("我肥胖", "我体重超标")),
        ("孕期", ("我怀孕", "我是孕妇", "我孕期")),
    ]
    for condition, phrases in pairs:
        if any(phrase in text for phrase in phrases):
            out.add(condition)
    return out


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
    conditions = detect_conditions(user_text)
    if not conditions:
        return {"profile_ready": True, "profile_missing": []}

    declared = _parse_declared_conditions(user_text) | _self_declared_conditions(user_text)
    missing = [c for c in conditions if not _declared_covers(c, declared)]
    if not missing:
        return {"profile_ready": True, "profile_missing": []}

    return {"profile_ready": False, "profile_missing": missing}


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
workflow.add_node("run_tools", tool_executor)#工具执行节点
workflow.add_node("condense_history", maybe_condense)# 注册长对话压缩节点
workflow.add_node("verify_answer", verify_answer_node)# 注册健康护栏审计节点
workflow.add_node("structure_answer", structure_answer_node)# 注册结构化回答节点
workflow.add_node("profile_gate", profile_gate_node)# 注册充分性门控节点（AgentMental 范式）
workflow.add_node("ask_user", ask_user_node)# 注册健康画像追问节点
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
    path=tools_condition,# LangGraph 内置工具判断函数上面调的库：会返回有tools或常量END
    path_map={#条件分支二选一对应目标节点
        "tools": "run_tools",#有就执行工具
        END: "verify_answer",#不调用工具→先过健康护栏审计，再结构化收尾
    },
)

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
              "degraded": "structure_answer"},#能用就节构化输出
)
# 3. 结构化回答完毕，整轮才真正结束
workflow.add_edge("structure_answer", END)
# 编译可运行的图，挂载 Sqlite 断点持久化
agent = workflow.compile(checkpointer=checkpointer)#创建可运行的图的实例对象
