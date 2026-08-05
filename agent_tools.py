# agent_tools.py：定义 Agent 可调用的工具（联网搜索菜谱 + 读取本地文件）
# 工具与图逻辑解耦：这里只管"工具本身怎么干活"，不涉及 LLM、状态图、断点等编排细节

import base64       # 把候选图片转成视觉模型可读取的 data URL
import os           # 读环境变量（UNSPLASH_ACCESS_KEY）
import re           # 解析 Bing 国内版返回 HTML 里的图片直链
import json#工具返回值打包成结构化 JSON（文本与图片 URL 分字段）
import requests#后端直接下载国内能访问的图片
from pathlib import Path#路径解析
from langchain_core.messages import HumanMessage  # 发送图片给视觉模型做内容校验
from langchain_core.tools import tool  # 创键工具
from langchain_tavily import TavilySearch#进行联网搜索
from model_name import get_langchain_llm  # 获取用于图片审核的视觉模型
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
# 图片搜索结果里出现这些词时，通常不是菜品成品图，直接跳过，避免字帖/广告/人物图进入 OSS。
BLOCKED_IMAGE_KEYWORDS = (
    "文字", "汉字", "字帖", "书法", "汽车", "房屋", "房子", "人物",
    "广告", "海报", "logo", "图标", "截图", "证件",
)
MAX_IMAGE_BYTES = 1_500_000  # 超过 1.5MB 不入对话，避免 base64 撑爆模型上下文
MIN_PHOTO_BYTES = 20_000  # 过小的图片通常是纯色占位图、文字缩略图或错误图片

# 图片先通过视觉模型审核，确认无误后才上传 OSS，避免无关图片污染图片桶。
_IMAGE_CHECK_LLM = None


def _recipe_image_matches(recipe_name: str, image_bytes: bytes, content_type: str) -> bool:
    """判断候选图是否真的是目标菜品成品图。

    只要无法确认是目标菜品，就返回 False，让上层继续尝试下一张候选图。
    这样宁可暂时无图，也不把建筑、风景或其他菜品错配到当前菜谱。
    """
    global _IMAGE_CHECK_LLM
    try:
        if _IMAGE_CHECK_LLM is None:
            _IMAGE_CHECK_LLM = get_langchain_llm(
                "gpt",
                temperature=0,
                max_tokens=30,
            )

        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:{content_type};base64,{image_base64}"
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "你是严格的菜品成品图审核器。"
                        f"目标菜名是：{recipe_name}。"
                        "只回答 YES 或 NO，不要解释。"
                        "只有在图片主体是可食用的成品菜，并且与目标菜名高度匹配时才回答 YES。"
                        "建筑、风景、人物、文字海报、餐具、包装、单独食材、"
                        "明显不同的菜品，或者无法确定时都回答 NO。"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                },
            ]
        )
        response = _IMAGE_CHECK_LLM.invoke([message])
        result = response.content
        if isinstance(result, list):
            result = " ".join(
                str(part.get("text", ""))
                for part in result
                if isinstance(part, dict)
            )
        result = str(result).strip().upper()
        matched = result.startswith("YES") or result.startswith("是")
        print(f"[image_check] {recipe_name} -> {'通过' if matched else '跳过'}")
        return matched
    except Exception as exc:
        # 审核模型不可用时不放行未审核图片，优先保证图片与菜名不乱配。
        print(f"[image_check] 审核失败，跳过候选图：{exc}")
        return False

# --------------------------------------------------------------------------- #
#  本地文件读取沙箱（安全红线）
#  get_file 只能读「项目内指定白名单目录」下的「白名单扩展名」文件，且单文件 ≤200KB。
#  目的：杜绝路径遍历读系统文件 / 密钥文件（如 .env），又不挡正常业务（菜谱库、偏好文件）。
# --------------------------------------------------------------------------- #
_PROJECT_ROOT = Path(__file__).resolve().parent
_ALLOWED_DIRS = [
    _PROJECT_ROOT / "data",                 # 用户偏好、营养表等本地知识
    _PROJECT_ROOT / "recipes",              # 用户私房菜谱库（离线、零成本、隐私）
    _PROJECT_ROOT / "resources" / "uploads",# 用户上传的图片/素材
]
_ALLOWED_EXT = {".txt", ".md", ".json", ".csv", ".yaml", ".yml"}
_MAX_FILE_BYTES = 200 * 1024  # 单文件上限 200KB，防超大文件撑爆上下文


def _resolve_safe_path(file_path: str):
    """把传入路径解析为绝对路径，并校验目录/扩展名/大小是否合规。
    合规返回 Path 对象；任一不合规返回 None（调用方据此返回拒绝提示）。"""
    try:
        p = Path(file_path).resolve()# 解析 ../ 与相对路径，防路径遍历
    except Exception:
        return None
    if p.suffix.lower() not in _ALLOWED_EXT:# 扩展名白名单
        return None
    # 目录白名单：解析后的绝对路径必须落在允许目录之下（含子目录）
    if not any(p.is_relative_to(d) for d in _ALLOWED_DIRS):
        return None
    return p


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


def _image_candidate_text(img) -> str:
    """提取 Tavily 图片候选的标题/描述，用于过滤明显无关图片。"""
    if isinstance(img, str):
        return ""
    if not isinstance(img, dict):
        return ""
    return " ".join(
        str(img.get(key, ""))
        for key in ("title", "description", "image_description", "alt")
    ).lower()


def to_data_url(img_list, recipe_name=None):
    # 内部函数：过滤国内无法访问的海外图源，并把能成功下载的图片转成 OSS URL 返回
    # 这里只返回真正可展示的图片 URL；找不到合适图片就返回空字符串，不再硬塞
    for img in img_list:
        img_url = img if isinstance(img, str) else img.get("url", "")#俩种图片返回格式都处理
        if not img_url.startswith(("http://", "https://")):#只要合法的 http(s) 链接
            continue
        candidate_text = _image_candidate_text(img)
        if any(word in candidate_text for word in BLOCKED_IMAGE_KEYWORDS):
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
            if len(resp.content) < MIN_PHOTO_BYTES:  # 过小图片通常不是可用的菜品成品图
                continue
            if not _looks_like_image(resp.content):  # 文件头不是真实图片，跳过
                continue
            if recipe_name and not _recipe_image_matches(
                recipe_name,
                resp.content,
                content_type,
            ):
                continue
            return upload_to_oss(resp.content, content_type)
        except Exception:
            continue
    return ""


# 国内可访问图源（Unsplash）兜底：Tavily 海外图被墙时，从 Unsplash CDN 拿家常菜图直链，
# 国内基本可达、秒级出图。需 UNSPLASH_ACCESS_KEY（免费申请于 unsplash.com/developers），
# 留空则用万相兜底。Unsplash 返回图床直链，仍走 to_data_url 校验+转自家 OSS。
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")


def _unsplash_image(query: str):
    """从 Unsplash 搜一道菜的成品图直链（国内 CDN 基本可达）。失败或无 key 返回 None。"""
    if not UNSPLASH_ACCESS_KEY:
        return None
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 5, "orientation": "square"},
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        for item in data.get("results") or []:
            urls = item.get("urls", {})
            raw = urls.get("small") or urls.get("regular") or urls.get("thumb")
            if raw:
                return raw
    except Exception:
        return None
    return None


# Bing 国内版图片搜索兜底：cn.bing.com 在国内直接可达、无需 key、覆盖家常菜。
# 返回 HTML 里抓图片直链；优先用 turl（Bing 自家缩略图 CDN，国内 100% 可达、不被防盗链），
# fallback murl（原始图直链，图床杂，可能被墙/防盗链）。
# 返回多张候选列表交给 to_data_url 逐张试——单张可能恰好下载失败（网络抖动），
# 多张候选把命中率拉满，与 Tavily images 列表的逐张试策略对齐。
def _bing_images(query: str, limit: int = 5):
    """从 Bing 国内版图片搜索拿一道菜的成品图候选直链列表（无需 key、国内可达）。
    返回 list（turl 优先、murl 补尾，最多 limit 张）；失败返回空 list。"""
    try:
        resp = requests.get(
            "https://cn.bing.com/images/search",
            params={"q": f"{query} 成品图", "form": "HDRSC2", "first": 1},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        # 优先 turl：Bing 自家缩略图 CDN（ts1-4.mm.bing.net），国内稳定可达
        turls = re.findall(r'turl&quot;:&quot;(.*?)&quot;', resp.text)
        if not turls:
            turls = re.findall(r'"turl":"(.*?)"', resp.text)
        candidates = [u for u in turls if u.startswith(("http://", "https://"))]
        # fallback murl 补尾：原始图直链，过滤 BLOCKED_DOMAINS
        if len(candidates) < limit:
            murls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', resp.text)
            if not murls:
                murls = re.findall(r'"murl":"(.*?)"', resp.text)
            for u in murls:
                if (
                    u.startswith(("http://", "https://"))
                    and not any(dom in u.lower() for dom in BLOCKED_DOMAINS)
                    and u not in candidates
                ):
                    candidates.append(u)
                if len(candidates) >= limit:
                    break
        return candidates[:limit]
    except Exception:
        return []


def find_recipe_image(recipe_name: str):
    """只为已经确定的最终菜名搜索一张对应成品图，返回 (OSS URL, 图源)。"""
    base_query = recipe_name.strip()
    if not base_query:
        return None, "none"

    image_friendly_query = f"{base_query} 菜品 美食 成品图"
    try:
        result = tavily.invoke({"query": image_friendly_query})
        image_url = to_data_url(
            result.get("images", []),
            recipe_name=base_query,
        ) or None
    except Exception:
        image_url = None

    if image_url is None:
        bing_candidates = _bing_images(f"{base_query} 菜品 美食")
        if bing_candidates:
            image_url = to_data_url(
                bing_candidates,
                recipe_name=base_query,
            ) or None

    if image_url is None:
        u = _unsplash_image(f"{base_query} food dish")
        if u:
            image_url = to_data_url(
                [u],
                recipe_name=base_query,
            ) or None

    if image_url is not None:
        return image_url, "real"

    ai_url = generate_dish_image(base_query)
    if ai_url:
        return ai_url, "ai"
    return None, "none"


@tool
def web_search(query: str) -> str:
    """当用户询问某道菜、食材搭配或家常菜做法时调用。
    工具只负责联网搜索文字做法；最终菜名确定后，由 find_recipe_image 单独搜索对应成品图。
    这样可以避免用户输入的食材名、口味词或泛查询直接被当成菜名搜图。"""
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
    # 图片不要在这里处理：此时 query 可能只是食材/口味，尚未确定最终菜名。
    # 等结构化节点选出唯一菜品后，再由 find_recipe_image 按最终菜名搜索，避免图文错配。
    image_url = None
    image_source = "none"

    # 关键：图片 URL 独立成字段，不再以 markdown 形式混进文本，
    # 从源头杜绝乱码 URL/二进制流被铺进回答；没图就老实给 None，不硬塞
    # image_source 随图返回，供 structure_answer 节点判定是否需要透明标注
    return json.dumps(
        {"text": "\n\n".join(lines), "image_url": image_url, "image_source": image_source},
        ensure_ascii=False,
    )

@tool
def get_file(file_path):
    """读取本地文本文件内容（受沙箱限制：仅允许 data/、recipes/、resources/uploads/ 下的
    txt/md/json/csv/yaml 文件，单文件 ≤200KB）。用于加载用户私房菜谱、偏好文件等本地知识。"""
    safe = _resolve_safe_path(file_path)
    if safe is None:
        return "读取被拒绝：该路径不在允许读取的目录内，或文件类型/大小超出限制。"
    try:
        if not safe.exists():
            return "文件不存在"
        if not safe.is_file():
            return "这不是一个文件"
        if safe.stat().st_size > _MAX_FILE_BYTES:
            return "文件过大，已拒绝读取（上限 200KB）"
        return safe.read_text(encoding="utf-8")
    except Exception as e:
        return f"读取失败：{e}"


# 所有工具的列表（交给 LLM 绑定 + ToolNode 统一执行）
tools = [web_search, get_file]#工具列表后面都会调用
