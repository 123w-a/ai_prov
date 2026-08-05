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
from agent_tools import find_recipe_image, tools, web_search
from agent_chains import build_structured_answer, rank_recipes#LCEL 结构化链(prompt|llm|parser)+排序+格式自动重试
#build_structured_answer标准链+parser检查出错误后再进行重试

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
#  只总结 user/AI，跳过 ToolMessage 工具返回；总结 Prompt 针对厨师场景定制
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
        "请把这段私厨对话历史压缩成极简要点，严格按以下规则：\n"
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
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要"):
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
    payload = [prompt_msg] + _drop_orphan_tool_messages(model_messages)#提示词+历史对话消息，括号是转换成列表
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
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要"):
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
        if allow_multiple:
            for index, recipe in enumerate(answer.recipes):
                recipe.image_url, recipe.image_ai_generated = _search_recipe_image(recipe.name)
                # 第一道菜兼容本轮已经拿到的图片，避免重复搜索失败时整轮无图。
                if not recipe.image_url and index == 0:
                    recipe.image_url = real_image_url
                    recipe.image_ai_generated = real_image_ai
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
#  5. 构建图实例（状态图 + 条件边 + 回边 = LangGraph 循环 Agent）
# --------------------------------------------------------------------------- #
# 创建LangGraph图状态机，通过节点流转规则读写、操控全局上下文容器MessagesState
workflow = StateGraph(MessagesState)#创建状态图，状态容器MessagesState作为参数
# 注册两个节点，左是节点名，右是函数
workflow.add_node("chef_think", chef_agent_node)#思考节点自己思考能看图片
workflow.add_node("run_tools", tool_executor)#工具执行节点
workflow.add_node("condense_history", maybe_condense)# 注册长对话压缩节点
workflow.add_node("structure_answer", structure_answer_node)# 注册结构化回答节点
workflow.set_entry_point("condense_history")# 设置入口：先压缩历史
workflow.add_edge("condense_history", "chef_think")#连线长对话压缩→思考

# 核心循环逻辑：条件边
# 1. LLM 思考完，判断是否要调用工具：要调用→去执行工具；不调用→去结构化收尾
workflow.add_conditional_edges(#条件分支边
    source="chef_think",#源节点
    path=tools_condition,# LangGraph 内置工具判断函数上面调的库：会返回有tools或常量END
    path_map={#条件分支二选一对应目标节点
        "tools": "run_tools",#有就执行工具
        END: "structure_answer",#不调用工具→去结构化节点整理最终回答
    },
)

# 2. 工具执行完毕，**回流到 LLM 节点再次思考（实现循环！）**
#    这就是 create_agent 做不到的闭环：工具结果回来重新让 LLM 校验、二次搜索、反思修
workflow.add_edge("run_tools", "chef_think")#再连线将工具执行结果回溯到思考节点
# 3. 结构化回答完毕，整轮才真正结束
workflow.add_edge("structure_answer", END)
# 编译可运行的图，挂载 Sqlite 断点持久化
agent = workflow.compile(checkpointer=checkpointer)#创建可运行的图的实例对象
