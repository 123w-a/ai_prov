# agent_graph.py：组装 LangGraph 循环 Agent（模型/断点/节点/状态图）
# 只负责"编排"：把提示词、工具、LLM、记忆、路由串成一张可运行的图
# 具体工具怎么干活看 agent_tools.py，提示词内容看 agent_prompts.py

import json#结构化回答打包成 JSON 字符串落进消息
import openai#捕获上游 LLM 偶发 5xx/超时异常做重试
import time#重试间隔用
import sqlite3#持久化短期记忆（断点续跑、循环状态保存）
from model_name import get_langchain_llm#传入模型
from langchain_core.messages import (  # 系统提示词节点用 + 长对话压缩用
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    RemoveMessage,
)
# LangGraph 核心替换导入：用 StateGraph 手动搭流程图，替代 create_agent 的线性执行
from langgraph.graph import StateGraph, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition  # 内置工具节点 + 是否继续调用工具的路由判断
from langgraph.checkpoint.sqlite import SqliteSaver#持久化短期记忆（断点续跑、循环状态保存）

from agent_prompts import SYSTEM_PROMPT
from agent_tools import tools
from agent_chains import chef_answer_chain, rank_recipes#LCEL 结构化链(prompt|llm|parser)+排序

# --------------------------------------------------------------------------- #
#  1. 模型 & 工具绑定
# --------------------------------------------------------------------------- #
provider = "gpt"#主脑运行模型
llm = get_langchain_llm(provider)#获取模型对象

# 绑定工具给LLM（让模型知道它能调用哪些工具）
llm_with_tools = llm.bind_tools(tools)

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
MAX_HISTORY_KEEP = 12  # 保留最近约 6 轮(user+ai)，更早的参与总结
#MessagesState是所有的状态消息，包含 messages 属性
def maybe_condense(state: MessagesState):#压缩历史对话
    msgs = state["messages"]
    # 最后一条消息不是用户发的或者没有消息的话就不执行后续操作
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {}
    # 只统计 user + ai 对话，跳开 ToolMessage 工具返回，避免把冗长搜索结果搅进摘要
    talk = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]#自动过滤没用消息
    if len(talk) <= MAX_HISTORY_KEEP:
        return {}  # 还没到阈值，不压缩
    keep = talk[-MAX_HISTORY_KEEP:]#切片表示从倒数第max_history_keep开始到末尾，新的要保留
    old = talk[:-MAX_HISTORY_KEEP]#从开始到倒数第max_history_keep，老的对话要总结后删除
    if not old:
        return {}
    convo = "\n".join(#将列表转为字符串供llm生成摘要
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}：{m.content}"#左边是词，右边是内容
        for m in old
    )
    summary_prompt = (#提示词
        "请把这段私厨对话历史压缩成极简要点，只保留：①用户拥有的食材 ②口味/忌口偏好 "
        "③已推荐或已做的菜谱 ④未完成的待办。用中文、分点、不超150字：\n\n" + convo
    )
    # LLM 偶发 502，简单重试，失败就先不压缩（不阻断主流程）
    summary = None#标记摘要是否成功生成
    for attempt in range(3):
        try:#服务器错误，网络错误，调用次数超过限制
            summary = llm.invoke([HumanMessage(content=summary_prompt)]).content#获取摘要
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError):
            time.sleep(1 + attempt)
    if not summary:#摘要生成失败就跳过
        return {}
    # 关键：被删 AIMessage 若带 tool_calls，它对应的 ToolMessage 必须"连坐"删除，
    # 否则孤儿 ToolMessage 留在历史里，下次请求 LLM 直接 400：
    # "No tool call found for function call output with call_id ..."
    doomed_call_ids = set()
    for m in old:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    doomed_call_ids.add(tc_id)
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

# --------------------------------------------------------------------------- #
#  4. 节点1：LLM 思考节点（绑定系统提示词 + 工具调用能力）
# --------------------------------------------------------------------------- #
def _drop_orphan_tool_messages(messages):
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


def chef_agent_node(state: MessagesState):
    messages = state["messages"]#已经被压缩过后的4种消息类的消息
    # 前置插入系统提示词，再追加历史对话消息（先清掉孤儿 ToolMessage 防 API 400）
    prompt_msg = SystemMessage(content=SYSTEM_PROMPT)
    payload = [prompt_msg] + _drop_orphan_tool_messages(messages)#提示词+历史对话消息，括号是转换成列表
    # 上游 LLM 偶发 502/超时，加重试避免整轮对话直接 500 崩掉
    last_err = None
    for attempt in range(3):
        try:
            resp = llm_with_tools.invoke(payload)
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError) as e:
            last_err = e#存储错误
            time.sleep(1 + attempt)  # 退避 1s / 2s 再试，但只试3次
    else:
        raise last_err  # 3 次都失败才真正抛出错误
    return {"messages": [resp]}

# 节点2：工具执行节点（自动调用 web_search / get_file）产出 ToolMessage
tool_executor = ToolNode(tools)#执行者

# --------------------------------------------------------------------------- #
#  4.5 节点3：结构化回答节点（LCEL 链登场的地方）
#  chef_think 决定不再调用工具时，不直接 END，而是来这里把整轮对话整理成
#  ChefAnswer JSON（菜名/星级/调料/步骤/图片/小建议），前端按卡片渲染。
#  失败降级：链跑挂了就什么都不加，历史里保留 chef_think 的 markdown 原文，
#  前端双格式兼容会按旧格式渲染，用户无感知。
# --------------------------------------------------------------------------- #
def _build_structure_context(messages):
    """给结构化链组装上下文：最近一条真实用户需求 + 本轮 web_search 的搜索结果。
    返回 (context_text, real_image_url)：real_image_url 是最近一次搜索真实拿到的
    图片链接（可能为 None），作为"有没有图"的代码级依据，不信模型口头说法。"""
    parts = []
    real_image_url = None
    # 最近一条真实用户消息（跳过压缩节点注入的"历史对话摘要"，图文混合消息只取文字）
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.content).startswith("[历史对话摘要"):
            if isinstance(m.content, list):#图文混合：图片已识别进对话，只取文字部分
                text = " ".join(
                    part.get("text", "") for part in m.content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                text = str(m.content)
            parts.append("用户需求：" + text)
            break
    # 本轮所有 web_search 工具返回（JSON 字符串：text 搜索结果 + image_url 图片）
    search_blocks = []
    for m in messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", "") == "web_search":
            content = str(m.content)
            search_blocks.append(content)
            try:#新格式 {text, image_url}；旧格式是纯文本，json 解析失败就跳过
                img = json.loads(content).get("image_url")
                if img:
                    real_image_url = img#后出现的覆盖前面的，留下最近一次
            except (ValueError, AttributeError):
                pass
    if search_blocks:
        parts.append(
            "搜索结果（每条是 JSON：text 为搜索文本、image_url 为成品图链接或 null）：\n"
            + "\n\n".join(search_blocks[-3:])#最多取最近3次搜索，防上下文过长
        )
    return "\n\n".join(parts), real_image_url


def structure_answer_node(state: MessagesState):
    messages = state["messages"]
    # chef_think 最后一轮的自然语言回答 = 流式已经推给前端的正文，原样保留进 opening
    opening = ""
    if messages and isinstance(messages[-1], AIMessage):
        opening = str(messages[-1].content)
    context, real_image_url = _build_structure_context(messages)
    if not context.strip():#没有可整理的上下文（理论上不会走到这），直接结束
        return {"messages": []}
    try:#结构化链也可能 502/JSON 校验失败，降级为"只用 markdown 原文"
        answer = chef_answer_chain.invoke({"context": context})
        answer = rank_recipes(answer)#排序不靠模型自觉，Python 精确执行
        # 代码兜底：图片 URL 以工具真实返回为准——有真链接才给图，没有就强制 null，
        # 杜绝"正文说找到图、卡片却没图"的口径不一
        answer.image_url = real_image_url
        if real_image_url is None and not answer.image_note:
            answer.image_note = "未找到可正常展示的成品图片。"
        payload = {"opening": opening, **answer.model_dump()}
        return {
            "messages": [
                AIMessage(content=json.dumps(payload, ensure_ascii=False))
            ]
        }
    except Exception:#降级：不追加任何消息，前端按旧 markdown 渲染 opening
        return {"messages": []}

# --------------------------------------------------------------------------- #
#  5. 构建图实例（状态图 + 条件边 + 回边 = LangGraph 循环 Agent）
# --------------------------------------------------------------------------- #
# 创建LangGraph图状态机，通过节点流转规则读写、操控全局上下文容器MessagesState
workflow = StateGraph(MessagesState)
# 注册两个节点，左是节点名，右是函数
workflow.add_node("chef_think", chef_agent_node)#思考节点
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
agent = workflow.compile(checkpointer=checkpointer)
