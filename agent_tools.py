# agent_tools.py：定义 Agent 可调用的工具（联网搜索菜谱 + 读取本地文件）
# 工具与图逻辑解耦：这里只管"工具本身怎么干活"，不涉及 LLM、状态图、断点等编排细节

import json#工具返回值打包成结构化 JSON（文本与图片 URL 分字段）
import requests#后端直接下载国内能访问的图片
from pathlib import Path#路径解析
from langchain_core.tools import tool  # 创键工具
from langchain_tavily import TavilySearch#进行联网搜索
from oss_utils import upload_to_oss  # 把成品图上传到OSS并返回公网URL
from image_gen import generate_dish_image  # 搜不到图时调通义万相生成「AI 示意图」兜底

# Tavily 搜索客户端（联网菜谱检索）
tavily = TavilySearch(
    max_results=2,                # 最多返回2条搜索结果
    search_depth="basic",         # 基础搜索深度（快速摘要，advanced会爬网页全文）
    include_answer=False,         # 不返回Tavily自带的总结答案
    include_raw_content=False,     # 不抓取网页原始完整HTML正文（省token、省钱）
    include_images=True,          # 开启搜索返回图片链接
    include_image_descriptions=True, # 给返回的每张图片附加文本描述
)

# 国内无法访问或容易返回视频/乱码的海外图源黑名单（省去无谓超时）
BLOCKED_DOMAINS = (
    "instagram.com", "facebook.com", "fbcdn.net", "pinterest.com",
    "pinimg.com", "t.co", "twitter.com", "x.com", "flickr.com",
    "imgur.com", "reddit.com", "wixmp.com", "ctcdn.co", "threads.com",
    "threads.net",
)
MAX_IMAGE_BYTES = 1_500_000  # 超过 1.5MB 不入对话，避免 base64 撑爆模型上下文


def _looks_like_image(data: bytes) -> bool:
    """用文件头 magic bytes 校验，防止服务器 Content-Type 撒谎。"""
    if len(data) < 12:
        return False
    return (
        data.startswith(b"\xff\xd8\xff")  # JPEG
        or data.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or data.startswith(b"GIF87a")  # GIF
        or data.startswith(b"GIF89a")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")  # WEBP
    )


def to_data_url(img_list):
    # 内部函数：过滤国内无法访问的海外图源，并把能成功下载的图片转成 OSS URL 返回
    # 这里只返回真正可展示的图片 URL；找不到合适图片就返回空字符串，不再硬塞
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
            if not _looks_like_image(resp.content):  # 文件头不是真实图片，跳过
                continue
            return upload_to_oss(resp.content, content_type)
        except Exception:
            continue
    return ""

@tool
def web_search(query: str) -> str:
    """当用户询问某道菜、食材搭配或家常菜做法时调用。
    工具会联网搜索文字做法，并尝试返回一张该菜品的成品图 OSS URL。
    为提升家常菜/常见菜的图片命中率，调用时给出明确菜名即可（如"红烧肉""糯米肉丸"），
    工具内部会自动追加美食/成品图关键词进行搜索。
    若搜索引擎完全拿不到可靠成品图，会自动调用通义万相生成一张「AI 示意图」兜底
    （该图会在下游被明确标注为 AI 生成，绝不伪装成真实成品照）。"""
    # 保留原始菜名查询，用于「AI 生图兜底」的提示词（不希望把"美食 成品图"后缀带进生图）
    base_query = query
    # 为提升成品图命中率，在查询中附加美食/成品图关键词（仍保留原意用于搜文字做法）
    image_friendly_query = f"{query} 美食 成品图"
    result = tavily.invoke({"query": image_friendly_query})  # 接受搜索到的json结果,拿文本

    items = result.get("results", [])#拿到result这个列表中的几条结果
    if not items:
        # 结构化空结果：image_url 为 None，下游节点据此在卡片里明说"没找到图"
        # image_source 标 "real" 表示并非 AI 生成（这里本来就无图）
        return json.dumps(
            {"text": "没有搜索到可靠结果", "image_url": None, "image_source": "real"},
            ensure_ascii=False,
        )

    lines = []#用来存初始化好了的结果
    for index, item in enumerate(items, start=1):#遍历结果的同时拿上序号
        url = item.get("url", "")
        # 长 URL 会撑破前端气泡，截断显示 + 做成可点击链接
        display_url = url if len(url) <= 50 else url[:47] + "..."
        lines.append(
            f"{index}. 标题：{item.get('title', '')}\n"
            f"来源：[{display_url}]({url})\n"
            f"摘要：{item.get('content', '')[:200]}"
        )
    images = result.get("images", [])#字典语法拿到图片路径，拿图片
    image_url = to_data_url(images) or None  # 在图片里挑一张国内可下载的，转成 OSS URL；没有就是 None
    image_source = "real"  # 图源标记：real=搜索实拍图 / ai=通义万相生成 / none=无图

    # 搜不到可靠成品图时，自动调用通义万相生成一张「AI 示意图」兜底（见 image_gen.py）。
    # 生成成功才把 image_source 置为 "ai"，下游据此在卡片上明示"AI 生成示意图"，避免误导。
    # 生成失败（无密钥/限流/异常）generate_dish_image 会返回 None，这里自然回退到"无图"。
    if image_url is None:
        ai_url = generate_dish_image(base_query)
        if ai_url:
            image_url = ai_url
            image_source = "ai"

    # 关键：图片 URL 独立成字段，不再以 markdown 形式混进文本，
    # 从源头杜绝乱码 URL/二进制流被铺进回答；没图就老实给 None，不硬塞
    # image_source 随图返回，供 structure_answer 节点判定是否需要透明标注
    return json.dumps(
        {"text": "\n\n".join(lines), "image_url": image_url, "image_source": image_source},
        ensure_ascii=False,
    )

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


# 所有工具的列表（交给 LLM 绑定 + ToolNode 统一执行）
tools = [web_search, get_file]#工具列表后面都会调用
