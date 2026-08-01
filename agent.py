from pathlib import Path#路径解析
import requests#后端直接下载国内能访问的图片
import time#重试间隔用
import openai#捕获上游 LLM 偶发 5xx/超时异常做重试
from model_name import get_langchain_llm#传入模型
from langchain_core.tools import tool  # 创键工具
from langchain_tavily import TavilySearch#进行联网搜索
from langchain_core.messages import (  # 系统提示词节点用 + 长对话压缩用
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    RemoveMessage,
)
import sqlite3#持久化短期记忆
# LangGraph 核心替换导入：用 StateGraph 手动搭流程图，替代 create_agent 的线性执行
from langgraph.graph import StateGraph, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition  # 内置工具节点 + 是否继续调用工具的路由判断
from langgraph.checkpoint.sqlite import SqliteSaver#持久化短期记忆（断点续跑、循环状态保存）
from oss_utils import upload_to_oss  #把成品图上传到OSS并返回公网URL

provider = "gpt"
llm=get_langchain_llm(provider)#获取模型对象

tavily = TavilySearch(
    max_results=2,                # 最多返回2条搜索结果
    search_depth="basic",         # 基础搜索深度（快速摘要，advanced会爬网页全文）
    include_answer=False,         # 不返回Tavily自带的总结答案
    include_raw_content=False,     # 不抓取网页原始完整HTML正文（省token、省钱）
    include_images=True,          # 开启搜索返回图片链接
    include_image_descriptions=True, # 给返回的每张图片附加文本描述
)

@tool
def web_search(query:str)-> str:
    """当用户给出食物图片或者食物文字的时候调用这个进行上网搜索要怎么搭配"""
    result = tavily.invoke({"query": query})#接受搜索到的json结果,拿文本

    items = result.get("results", [])#拿到result这个列表中的几条结果
    if not items:
        return "没有搜索到可靠结果"#结束对话防止生成垃圾数据

    lines = []#用来存初始化好了的结果
    for index, item in enumerate(items, start=1):#遍历结果的同时拿上序号
        lines.append(
            f"{index}. 标题：{item.get('title', '')}\n"
            f"URL：{item.get('url', '')}\n"
            f"摘要：{item.get('content', '')[:200]}"
        )
    images = result.get("images", [])#字典语法拿到图片路径，拿图片
    # 内部函数：过滤国内无法访问的海外图源，并把能成功下载的图片转成 data URL 内嵌
    # 这样前端不再依赖任何第三方图片代理，图片 100% 能直接显示
    BLOCKED_DOMAINS = (
        "instagram.com", "facebook.com", "fbcdn.net", "pinterest.com",
        "pinimg.com", "t.co", "twitter.com", "x.com", "flickr.com",
        "imgur.com", "reddit.com", "wixmp.com", "ctcdn.co",
    )
    MAX_IMAGE_BYTES = 1_500_000  # 超过 1.5MB 不入对话，避免 base64 撑爆模型上下文

    def to_data_url(img_list):
        for img in img_list:
            img_url = img if isinstance(img, str) else img.get("url", "")#俩种图片返回格式都处理
            if not img_url.startswith(("http://", "https://")):#只要合法的 http(s) 链接
                continue
            if any(dom in img_url.lower() for dom in BLOCKED_DOMAINS):  # 跳过明显被墙的海外图源，省去无谓超时
                continue
            try:
                resp = requests.get(
                    img_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}
                )
                content_type = resp.headers.get("Content-Type", "")
                if resp.status_code != 200 or not content_type.startswith("image/"):
                    continue
                if len(resp.content) > MAX_IMAGE_BYTES:  # 太大就放弃，尝试下一张
                    continue
                return upload_to_oss(resp.content, content_type)
            except Exception:
                continue
        return ""

    data_url = to_data_url(images)#在图片里挑一张国内可下载的，转成 data URL

    if data_url:#成功下载才放入回答
        image_line = f"\n\n唯一成品图：![成品图]({data_url})"
    else:
        image_line = "\n\n未找到可正常展示的成品图片。"

    return "\n\n".join(lines) + image_line#从列表变为字符串再拼接上变为markdown格式的图片格式

@tool
def get_file(file_path):
    """读取本地文本文件内容的时候使用"""
    try:
        path = Path(file_path)#转为路径对象

        if not path.exists():
            return "文件不存在"

        if not path.is_file():
            return "这不是一个文件"

        return path.read_text(encoding="utf-8")
    except Exception as e:
        return f"读取失败：{e}"



# 绑定工具给LLM（让模型知道它能调用哪些工具）
tools = [web_search, get_file]
llm_with_tools = llm.bind_tools(tools)

connection = sqlite3.connect(
    database="resources/checkpoint.db",
    check_same_thread=False
)

checkpointer = SqliteSaver(connection)
checkpointer.setup()

# 系统提示词 原样搬过来（一字不改）
SYSTEM_PROMPT = """
你是一位ai私厨助手，严格按照以下6个核心功能依次执行任务，执行步骤不可跳过：
1. 图片识别：接收用户上传食材图片/手动输入食材名称，或者用户给定的文件夹中的图片中精准识别所有可见食材，剔除餐具、调料瓶等非食用物品，清晰列出食材清单；若图片模糊无法识别，直接提示用户重新上传或手动打字输入食材。
2. 智能搜索：基于识别出的全部食材，调用web_search这个工具检索可利用现有食材制作的家常菜、快手菜食谱，优先使用存量食材，尽量减少额外采购辅料。
3. 智能排序：对检索到的所有食谱进行双重排序，第一优先级：营养价值从高到低排序；第二优先级：制作难度由易到难排序，每个食谱标注【难度等级】【营养亮点】，可以每一个标准都是最大5颗星星，比如：有3颗就在5颗暗的星星上亮3颗，暗2颗，
4. 创意建议：若可用食材搭配极少、常规菜谱不足，主动给出多组创意混搭吃法、简易快手料理方案，兼顾美味就行
5. 对话交互：全程保持聊天式自然对话风格，在说明做菜细节的时候说出克数的同时可以用生活中常见的大小或者多少来比喻要放的克重，支持用户继续追问做法细节、替换食材、调整口味（辣/清淡/减脂），可接收新一轮图片上传二次识别。
6.当给出使用流程后可以给出自己作为厨师的一个建议，建议要简短，3句就够了
约束规则：
1. 回答结构清晰，分点排版，不用冗余废话；
2. 食谱步骤通俗易懂，适合家庭厨房操作；
3. 减脂、高营养菜品重点标注；
4. 要重点说明制作的方法，你是厨师不能一笔带过，要严谨。
5.允许用户随时打断，修改需求。
6.严格按照用户的需求，比如：用户说重辣的话，那给的图片也要是很辣的感觉
7.每次返回可以多次调用web_search，但是必须要遵守 max_results设定的返回条数不能超， 
图片规则：
图片输出规则：
1. 每次最终回答最多只能输出一张图片。
2. 图片只能放在全部食谱和制作步骤之后。
3. 只能选择 web_search 返回的第一张有效图片。
4. 禁止输出第二张图片、图片列表或多个 Markdown 图片链接。
5. 如果没有可用图片，必须写“未找到可正常展示的成品图片”，不要输出损坏的图片链接。
6. 不要自己编造图片地址。
7.联网没有找到的话，必须要说没有找到合适的但要给用户描述一下口感，无论找没找到都要说
"""

# --------------------------------------------------------------------------- #
#  LangGraph 循环 Agent：替代原来的 create_agent（线性单次执行）
#  流程图：condense_history(压缩) -> chef_think(LLM决策) -> [需要工具?] -> run_tools(执行工具) -> 回流 chef_think
#  关键点：工具结果回来后重新让 LLM 校验、二次搜索、反思修正，直到模型不再调用工具才结束
# --------------------------------------------------------------------------- #

# 长对话压缩节点（替代原 SummarizationMiddleware）：LLM 推理前触发
# 历史消息超过阈值时，把更老的"用户/AI 对话"总结成要点、删掉原文，防上下文溢出
# 只总结 user/AI，跳过 ToolMessage 工具返回；总结 Prompt 针对厨师场景定制
MAX_HISTORY_KEEP = 12  # 保留最近约 6 轮(user+ai)，更早的参与总结

def maybe_condense(state: MessagesState):
    msgs = state["messages"]
    # 只在"用户刚发新消息"这一轮压缩；工具回流(末尾是 AI/Tool 消息)不重复压缩，省调用费
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return {}
    # 只统计 user + ai 对话，跳开 ToolMessage 工具返回，避免把冗长搜索结果搅进摘要
    talk = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]
    if len(talk) <= MAX_HISTORY_KEEP:
        return {}  # 还没到阈值，不压缩
    keep = talk[-MAX_HISTORY_KEEP:]      # 最近几轮原样保留
    old = talk[:-MAX_HISTORY_KEEP]       # 更老的参与总结后删除
    if not old:
        return {}
    convo = "\n".join(
        f"{'用户' if isinstance(m, HumanMessage) else 'AI'}：{m.content}"
        for m in old
    )
    summary_prompt = (
        "请把这段私厨对话历史压缩成极简要点，只保留：①用户拥有的食材 ②口味/忌口偏好 "
        "③已推荐或已做的菜谱 ④未完成的待办。用中文、分点、不超150字：\n\n" + convo
    )
    # LLM 偶发 502，简单重试，失败就先不压缩（不阻断主流程）
    summary = None
    for attempt in range(3):
        try:
            summary = llm.invoke([HumanMessage(content=summary_prompt)]).content
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError):
            time.sleep(1 + attempt)
    if not summary:
        return {}
    # 删除被总结掉的旧消息、插入摘要（RemoveMessage 真正从 state 移除，避免无限增长）
    removals = [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None)]
    summary_msg = HumanMessage(content=f"[历史对话摘要，供参考]\n{summary}")
    return {"messages": removals + [summary_msg]}

# 节点1：LLM 思考节点（绑定系统提示词 + 工具调用能力）
def chef_agent_node(state: MessagesState):
    messages = state["messages"]
    # 前置插入系统提示词，再追加历史对话消息
    prompt_msg = SystemMessage(content=SYSTEM_PROMPT)
    payload = [prompt_msg] + messages
    # 上游 LLM 偶发 502/超时，加重试避免整轮对话直接 500 崩掉
    last_err = None
    for attempt in range(3):
        try:
            resp = llm_with_tools.invoke(payload)
            break
        except (openai.InternalServerError, openai.APIConnectionError, openai.RateLimitError) as e:
            last_err = e
            time.sleep(1 + attempt)  # 退避 1s / 2s 再试
    else:
        raise last_err  # 3 次都失败才真正抛出
    return {"messages": [resp]}

# 节点2：工具执行节点（自动调用 web_search / get_file）
tool_executor = ToolNode(tools)

# 构建图实例
workflow = StateGraph(MessagesState)

# 注册两个节点
workflow.add_node("chef_think", chef_agent_node)
workflow.add_node("run_tools", tool_executor)

# 注册长对话压缩节点
workflow.add_node("condense_history", maybe_condense)

# 设置入口：先压缩历史，再进入 LLM 思考（压缩时机固定为 LLM 推理前，符合规范）
workflow.set_entry_point("condense_history")
workflow.add_edge("condense_history", "chef_think")

# 核心循环逻辑：条件边
# 1. LLM 思考完，判断是否要调用工具：要调用→去执行工具；不调用→直接结束
workflow.add_conditional_edges(
    source="chef_think",
    path=tools_condition,  # LangGraph 内置工具判断函数：有 tool_call 走 tools，否则结束
    path_map={
        "tools": "run_tools",
        END: END,
    },
)

# 2. 工具执行完毕，**回流到 LLM 节点再次思考（实现循环！）**
#    这就是 create_agent 做不到的闭环：工具结果回来重新让 LLM 校验、二次搜索、反思修正
workflow.add_edge("run_tools", "chef_think")

# 编译可运行的图，挂载 Sqlite 断点持久化（thread_id 对应 checkpoint.db 里的单条任务断点）
agent = workflow.compile(checkpointer=checkpointer)
