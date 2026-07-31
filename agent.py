from pathlib import Path#路径解析
from urllib.parse import quote#编码转义
from langchain.agents.middleware import SummarizationMiddleware
from model_name import get_langchain_llm#传入模型
from langchain.agents import create_agent  # 创键agent
from langchain_core.tools import tool  # 创键工具
from langchain_tavily import TavilySearch#进行联网搜索
import sqlite3#持久化短期记忆
from langgraph.checkpoint.sqlite import SqliteSaver#持久化短期记忆

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
    # 内部函数：只获取第一张合法http/https图片链接
    def get_one_image_url(img_list):
        for img in img_list:
            img_url = img if isinstance(img, str) else img.get("url", "")#俩种图片返回格式都处理
            if img_url.startswith(("http://", "https://")):#找到了就返回，一次调用工具返回一张
                return img_url
        return ""

    one_image_url = get_one_image_url(images)#在提取出的俩种图片格式下返回第一种图片URL

    if one_image_url:#有图片URL再处理
        display_url = "https://images.weserv.nl/?url=" + quote(one_image_url, safe="")
        image_line = f"\n\n唯一成品图：![成品图]({display_url})"
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



connection = sqlite3.connect(
    database="resources/checkpoint.db",
    check_same_thread=False
)

checkpointer = SqliteSaver(connection)
checkpointer.setup()

middleware = SummarizationMiddleware(
    model="deepseek-v4-pro",#总结模型
    trigger=("messages", 20),#到10条就总结的阈值
    keep=("messages", 5)#保留几条消息的阈值
)


agent = create_agent(
    model=llm,#大脑
    tools=[web_search,get_file],
    system_prompt="""
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
""",
    checkpointer=checkpointer,
    middleware=[middleware]#增加长对话自动总结中间件
)