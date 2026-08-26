# agent_tools.py：定义 Agent 可调用的工具（联网搜索菜谱 + 读取本地文件）
# 工具与图逻辑解耦：这里只管"工具本身怎么干活"，不涉及 LLM、状态图、断点等编排细节

import base64       # 把候选图片转成视觉模型可读取的 data URL
import os           # 读环境变量（UNSPLASH_ACCESS_KEY）
import re           # 解析 Bing 国内版返回 HTML 里的图片直链
import csv          # 读取结构化营养表 nutrition_table.csv
import json#工具返回值打包成结构化 JSON（文本与图片 URL 分字段）
import requests#后端直接下载国内能访问的图片
from pathlib import Path#路径解析
from langchain_core.messages import HumanMessage  # 发送图片给视觉模型做内容校验
from langchain_core.tools import tool  # 创键工具
from langchain_tavily import TavilySearch#进行联网搜索
from model_name import get_langchain_llm  # 获取用于图片审核的视觉模型
from oss_utils import upload_to_oss  # 把成品图上传到OSS并返回公网URL
from image_gen import generate_dish_image  # 搜不到图时调通义万相生成「AI 示意图」兜底

# Tavily 搜索客户端（联网菜谱检索）。
# 未配置 TAVILY_API_KEY 时优雅降级：tavily 置 None，web_search 返回友好提示、
# 成品图自动走 Bing/Unsplash/AI 生图兜底链；在 .env 补上 key 重启即可恢复。
tavily = None
try:
    tavily = TavilySearch(
        max_results=2,                # 最多返回2条搜索结果
        search_depth="basic",         # 基础搜索深度（快速摘要，advanced会爬网页全文）
        include_answer=False,         # 不返回Tavily自带的总结答案
        include_raw_content=False,     # 不抓取网页原始完整HTML正文（省token、省钱）
        include_images=True,          # 开启搜索返回图片链接
        include_image_descriptions=True, # 给返回的每张图片附加文本描述
    )
except Exception as _tavily_exc:
    print(f"[agent_tools] Tavily 未配置或不可用，web_search 已降级：{_tavily_exc}")

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
    global _IMAGE_CHECK_LLM#用外部定义的语言模型
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


def to_data_url(img_list, recipe_name=None):#候选图片验证并上传 OSS
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

# 高德 Web 服务 Key（POI 餐厅检索）：在 .env 配置 AMAP_KEY。留空则 nearby_food 走 mock 兜底。
# 个人开发者免费、约 5000 次/天，无需付费；本地测试 IP 白名单可先留空，上线再加。
AMAP_KEY = os.getenv("AMAP_KEY", "")


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


def find_recipe_image(recipe_name: str):#搜索一张对应成品图
    """只为已经确定的最终菜名搜索一张对应成品图，返回 (OSS URL, 图源)。"""
    base_query = recipe_name.strip()#清洗字符串
    if not base_query:
        return None, "none"

    image_friendly_query = f"{base_query} 菜品 美食 成品图"
    try:
        result = tavily.invoke({"query": image_friendly_query})#去搜这个关键词
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
    if tavily is None:
        return json.dumps(
            {"text": "联网搜索未启用：缺少 TAVILY_API_KEY，请在 .env 中配置后重启后端。",
             "image_url": None, "image_source": "real"},
            ensure_ascii=False,
        )
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


# --------------------------------------------------------------------------- #
#  外食 / 懒人点单导航（波次 A4）：附近餐厅推荐 + 菜系护栏
#  真实部署：接入高德 Web 服务 POI API（.env 配置 AMAP_KEY）。
#  无 key / 断网 / 接口报错时自动回退内置 mock 数据集，保证 demo 不挂。
# --------------------------------------------------------------------------- #
_MOCK_RESTAURANTS = [
    {"name": "轻食说·沙拉碗", "cuisine": "轻食", "avg_price": 38, "distance_km": 0.6,
     "guardrail": "优先油醋汁、少酱；避开培根/芝士增量"},
    {"name": "老街家常菜", "cuisine": "家常菜", "avg_price": 45, "distance_km": 1.2,
     "guardrail": "点蒸煮炖、避开红烧/干锅；叮嘱少油少盐"},
    {"name": "渝味火锅", "cuisine": "火锅", "avg_price": 80, "distance_km": 0.9,
     "guardrail": "清汤/番茄锅底；多涮菜少肉丸；蘸料避芝麻酱用醋+蒜"},
    {"name": "街角烧烤", "cuisine": "烧烤", "avg_price": 60, "distance_km": 1.5,
     "guardrail": "高血压/痛风避开啤酒+内脏+海鲜；多选烤蔬菜"},
    {"name": "幸福食堂快餐", "cuisine": "快餐", "avg_price": 25, "distance_km": 0.4,
     "guardrail": "选杂粮饭+清炒时蔬+蒸蛋；避开油炸窗口"},
]


def _infer_guardrail(name: str, ptype: str, cost=None) -> str:
    """按店名/餐饮类型粗判点单红线（健康护栏）。高德不提供，是我们产品的差异点。"""
    text = f"{name} {ptype}"
    if "火锅" in text:
        return "清汤/番茄锅底；多涮菜少肉丸；蘸料避芝麻酱用醋+蒜"
    if "烧烤" in text or "烤肉" in text:
        return "高血压/痛风避开啤酒+内脏+海鲜；多选烤蔬菜"
    if "轻食" in text or "沙拉" in text or "西餐" in text:
        return "优先油醋汁、少酱；避开培根/芝士增量"
    if "快餐" in text or "小吃" in text or "炸" in text:
        return "选杂粮饭+清炒时蔬+蒸蛋；避开油炸窗口"
    if "家常" in text or "中餐" in text or "炒菜" in text:
        return "点蒸煮炖、避开红烧/干锅；叮嘱少油少盐"
    return "优先蒸煮炖凉拌、少油少盐；控制份量与主食比例"


def _amap_poi_search(city, district, query, budget, location=""):
    import re as _re
    # 坐标清洗：模型可能把「[当前位置：经度,纬度]」整串塞进 location，只提取两个浮点数，
    # 否则 amap 判定非法坐标退化为城市文本搜索，距离全是错位数字（如 128km）。
    _m = _re.search(r"(\d{1,3}\.\d{3,})[,\s，]+(\d{1,3}\.\d{3,})", str(location or ""))
    location = f"{_m.group(1)},{_m.group(2)}" if _m else ""
    """调用高德 POI 接口，返回标准化候选列表；无 key / 断网 / 接口报错返回 None（触发 mock 兜底）。
    location 为 '经度,纬度' 时走周边搜索（带真实距离），否则走文本搜索（无距离）。"""
    if not AMAP_KEY:
        return None
    keyword = query.strip() if query else "餐厅"
    city_param = (city or "").strip()
    if district and district.strip():
        city_param = f"{city_param}{district.strip()}" if city_param else district.strip()

    around = bool(location) and "," in str(location)
    common = {
        "key": AMAP_KEY,
        "keywords": keyword,
        "types": "050000",          # 餐饮服务大类
        "extensions": "all",        # 要拿 biz_ext.cost（人均）
        "offset": 25,
        "page": 1,
    }
    if around:
        # 周边搜索：需要圆心经纬度，返回真实 distance（米）
        url = "https://restapi.amap.com/v3/place/around"
        params = {**common, "location": location, "radius": 3000}
    else:
        url = "https://restapi.amap.com/v3/place/text"
        params = {**common, "city": city_param,
                  "citylimit": "true" if city_param else "false"}

    try:
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json()
    except Exception:
        return None
    if data.get("status") != "1":   # 高德 status=0 表示 key 无效 / 超限 / 配额耗尽
        return None
    pois = data.get("pois", []) or []
    results = []
    for p in pois:
        biz = p.get("biz_ext") or {}
        if not isinstance(biz, dict):
            biz = {}
        cost_str = (biz.get("cost") or "").strip()
        try:
            cost = int(float(cost_str))
        except Exception:
            cost = None
        raw_type = p.get("type", "") or ""
        cuisine = raw_type.split(";")[-1] if raw_type else "餐饮"
        dist_m = p.get("distance")   # 仅周边搜索返回（米）
        try:
            distance_km = round(int(dist_m) / 1000, 1) if dist_m not in (None, "") else None
        except Exception:
            distance_km = None
        results.append({
            "name": p.get("name", ""),
            "cuisine": cuisine,
            "avg_price": cost if cost is not None else 0,
            "distance_km": distance_km,
            "address": p.get("address", "") or "",
            "guardrail": _infer_guardrail(p.get("name", ""), raw_type, cost),
        })
    return results


@tool
def nearby_food(city: str = "", district: str = "", budget: int = 50, query: str = "", location: str = "") -> str:
    """当用户说"附近/随便/不知道吃啥/外卖/点单/懒得做"等在外或懒得做饭场景时调用。
    按预算(budget,单位元)与菜系护栏过滤附近餐厅，返回店名、人均、距离与点单红线。
    真实环境接入高德 POI API（.env 配 AMAP_KEY）；无 key / 断网时自动回退内置 mock 兜底。
    location 可传 '经度,纬度'（如前端定位），传了就走周边搜索给出真实距离；不传则按城市文本搜索。"""
    try:
        budget = int(budget) if budget else 50
    except Exception:
        budget = 50

    real = _amap_poi_search(city, district, query, budget, location)
    if real is None:
        candidates = _MOCK_RESTAURANTS
        source = "mock"          # 无 key / 断网 / 接口报错 → 兜底
    elif real:
        candidates = real
        source = "amap"
    else:
        candidates = _MOCK_RESTAURANTS
        source = "mock_empty"    # 联网成功但 0 命中 → 仍用 mock 顶上

    # 预算过滤：amap 仅保留有人均且≤预算者（无人均不误杀）；mock 按原价位
    if source == "amap":
        priced = [c for c in candidates if c["avg_price"] and c["avg_price"] <= budget]
        candidates = priced if priced else candidates
    else:
        under = [c for c in candidates if c["avg_price"] <= budget]
        candidates = under if under else _MOCK_RESTAURANTS[:3]

    # query 命中菜系/店名/地址时优先排前
    if query:
        q = query.strip()
        candidates.sort(
            key=lambda c: (
                q not in c["name"]
                and q not in c["cuisine"]
                and q not in c.get("address", "")
            )
        )
    if not candidates:
        candidates = _MOCK_RESTAURANTS[:3]

    lines = []
    for i, c in enumerate(candidates[:5], 1):
        dist = c.get("distance_km")
        dist_text = f"{dist}km" if dist is not None else "约—（未定位）"
        addr = c.get("address", "")
        addr_text = f"｜{addr}" if addr else ""
        lines.append(
            f"{i}. {c['name']}（{c['cuisine']}）｜人均约¥{c['avg_price'] or '?'}｜{dist_text}{addr_text}\n"
            f"   点单红线：{c['guardrail']}"
        )
    return json.dumps(
        {"city": city, "district": district, "budget": budget,
         "count": len(lines), "source": source, "text": "\n\n".join(lines)},
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
#  营养计算辅助（波次 A5）：食物热量查表 + 运动当量换算
#  数据源（双表）：
#   ① 主表 data/china_food_components.csv —— 国标《中国食物成分表》常用食材（258 种，13 字段，最权威）
#   ② 兜底 data/nutrition_table.csv —— 含主表未收的加工/快餐/饮料（炸鸡/可乐/奶茶等）
#  口语别名 _FOOD_ALIAS 桥接"米饭↔大米（粳米）"等细化名，避免查不到。
#  均未收录时如实说明，不臆造数值。
# --------------------------------------------------------------------------- #
_FOOD_KCAL_PER_100G = {
    "蛋挞": 290, "米饭": 116, "馒头": 223, "面条": 110, "白面包": 265,
    "鸡胸肉": 133, "鸡蛋": 144, "牛奶": 54, "苹果": 52, "香蕉": 93,
    "可乐": 43, "奶茶": 70, "炸鸡": 280, "薯条": 298, "番茄": 18,
    "黄瓜": 16, "牛肉": 250, "猪肉": 395, "三文鱼": 208, "豆腐": 76,
    "酸奶": 72, "啤酒": 43, "西瓜": 30, "燕麦": 389, "花生": 567,
}

# 口语名 → 权威表里的"细化名"别名（解决 米饭↔大米（粳米） 这类查不到的问题）
_FOOD_ALIAS = {
    "米饭": "大米（粳米）", "白米饭": "大米（粳米）", "白米": "大米（粳米）",
    "米": "大米（粳米）", "馒头": "馒头（蒸）", "面条": "面条（挂面）",
}


def _load_china_food_table():
    """从 data/china_food_components.csv（国标《中国食物成分表》）加载权威营养表。
    中文表头映射为统一字段；文件缺失/读失败返回空列表（触发兜底，不报错）。"""
    rows = []
    csv_path = _PROJECT_ROOT / "data" / "china_food_components.csv"
    try:
        if not csv_path.exists():
            return rows
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                name = (r.get("食物名称") or "").strip()
                if not name:
                    continue

                def _num(v):
                    try:
                        return float(v)
                    except Exception:
                        return None

                rows.append({
                    "name": name,
                    "kcal": _num(r.get("能量kcal")),
                    "protein": _num(r.get("蛋白质g")),
                    "fat": _num(r.get("脂肪g")),
                    "carb": _num(r.get("碳水化合物g")),
                    "fiber": _num(r.get("膳食纤维g")),
                    "sodium": _num(r.get("钠mg")),
                    "calcium": _num(r.get("钙mg")),
                    "iron": _num(r.get("铁mg")),
                    "vitamin_c": _num(r.get("维生素Cmg")),
                    "category": (r.get("分类") or "").strip(),
                })
    except Exception:
        return []
    return rows


_CHINA_FOOD_TABLE = _load_china_food_table()


def _load_nutrition_table():
    """从 data/nutrition_table.csv 加载结构化营养表；文件缺失/读失败返回空列表（触发兜底）。"""
    rows = []
    csv_path = _PROJECT_ROOT / "data" / "nutrition_table.csv"
    try:
        if not csv_path.exists():
            return rows
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                name = (r.get("name") or "").strip()
                if not name:
                    continue

                def _num(v):
                    try:
                        return float(v)
                    except Exception:
                        return None

                rows.append({
                    "name": name,
                    "kcal": _num(r.get("kcal_per_100g")),
                    "protein": _num(r.get("protein_g")),
                    "fat": _num(r.get("fat_g")),
                    "carb": _num(r.get("carb_g")),
                    "category": (r.get("category") or "").strip(),
                })
    except Exception:
        return []
    return rows


_NUTRITION_TABLE = _load_nutrition_table()


@tool
def calorie_lookup(food: str, portion_g: int = 100) -> str:
    """查询某食物的热量与营养素。food=食物名，portion_g=份量(克,默认100)。
    优先查国标《中国食物成分表》(data/china_food_components.csv)，再回退营养骨架表(加工/快餐类)，
    口语名经 _FOOD_ALIAS 桥接；返回份量 kcal + 已收录营养素 + source 透明标注。
    配合 exercise_equiv 换算运动量；未收录/无数值时如实说明，不臆造。"""
    key = food.strip()
    # 口语别名桥接（米饭→大米（粳米）等），解决权威表"细化名"查不到
    key = _FOOD_ALIAS.get(key, key)

    # 第一优先：国标权威表 china_food_components.csv（258 种，字段最全）
    hit = None
    for row in _CHINA_FOOD_TABLE:
        if row["name"] == key:
            hit = row
            break
    if hit is None:
        for row in _CHINA_FOOD_TABLE:
            if key in row["name"] or row["name"] in key:
                hit = row
                break

    if hit is not None:
        per100 = hit["kcal"]
        if per100 is None:
            return json.dumps(
                {"food": food, "kcal": None,
                 "note": "该食物热量未录入，请用权威食物成分表查询；此处不臆造数值"},
                ensure_ascii=False,
            )
        kcal = round(per100 * portion_g / 100)
        result = {
            "food": food, "portion_g": portion_g, "kcal": kcal,
            "per_100g": per100, "source": "china_food_table",
            "note": "数据来自国标《中国食物成分表》，权威值",
        }
        for label, val in (("protein_g", hit["protein"]), ("fat_g", hit["fat"]),
                           ("carb_g", hit["carb"]), ("fiber_g", hit["fiber"]),
                           ("sodium_mg", hit["sodium"]), ("calcium_mg", hit["calcium"]),
                           ("iron_mg", hit["iron"]), ("vitamin_c_mg", hit["vitamin_c"])):
            if val is not None:
                result[label] = round(val * portion_g / 100, 1)
        if hit["category"]:
            result["category"] = hit["category"]
        return json.dumps(result, ensure_ascii=False)

    # 第二优先：营养骨架表 nutrition_table.csv（含权威表未收的加工/快餐/饮料，如炸鸡/可乐/奶茶）
    hit = None
    for row in _NUTRITION_TABLE:
        if row["name"] == key:
            hit = row
            break
    if hit is None:
        for row in _NUTRITION_TABLE:
            if key in row["name"] or row["name"] in key:
                hit = row
                break

    if hit is not None:
        per100 = hit["kcal"]
        if per100 is None:
            return json.dumps(
                {"food": food, "kcal": None,
                 "note": "该食物热量未录入，请用权威食物成分表查询；此处不臆造数值"},
                ensure_ascii=False,
            )
        kcal = round(per100 * portion_g / 100)
        result = {
            "food": food, "portion_g": portion_g, "kcal": kcal,
            "per_100g": per100, "source": "builtin",
            "note": "营养骨架表估算值（加工/快餐类），精确值以《中国食物成分表》为准",
        }
        macros = {}
        for label, val in (("protein_g", hit["protein"]),
                           ("fat_g", hit["fat"]),
                           ("carb_g", hit["carb"])):
            if val is not None:
                macros[label] = round(val * portion_g / 100, 1)
        if macros:
            result["macros"] = macros
        if hit["category"]:
            result["category"] = hit["category"]
        return json.dumps(result, ensure_ascii=False)

    # 都未命中 → 如实说明，不臆造
    return json.dumps(
        {"food": food, "kcal": None,
         "note": "权威表与营养骨架表均未收录，请用《中国食物成分表》查询；此处不臆造数值"},
        ensure_ascii=False,
    )


@tool
def exercise_equiv(kcal: int, weight_kg: float = 60.0) -> str:
    """把摄入热量换算成运动当量，让用户直观感受。kcal=摄入千卡，weight_kg=体重(默认60)。
    用常见运动燃脂速率估算：慢跑≈0.115 kcal/kg/分钟，骑车≈0.08，快走≈0.06。
    返回各项运动约需分钟数。写"约"，强调估算性质。"""
    try:
        kcal = float(kcal)
        weight_kg = float(weight_kg) or 60.0
    except Exception:
        return json.dumps({"error": "kcal/weight 需为数字"}, ensure_ascii=False)
    rates = {"慢跑": 0.115, "骑车": 0.08, "快走": 0.06}
    result = {act: max(1, round(kcal / (rate * weight_kg))) for act, rate in rates.items()}
    return json.dumps(
        {"kcal": kcal, "weight_kg": weight_kg, "minutes": result,
         "note": "约算值（中等强度），仅作直观参考"},
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
#  权威知识检索（波次 P0①）：把已索引的国家膳食/慢病指南接进 Agent
#  这是"权威护栏 / 可溯源"的落地关键——Agent 从此能查那 11 份权威 PDF，
#  而不是只靠联网搜索。rag.search 已含混合检索 + 重排，返回带 source 文件名。
# --------------------------------------------------------------------------- #
@tool
def nutrition_kb_search(query: str, top_k: int = 3) -> str:
    """当用户询问与"健康饮食原则、慢病忌口、营养标签、某种食物能不能吃"相关的问题时调用。
    例如：高血压能吃咸鸭蛋吗、糖尿病饮食注意什么、痛风忌口、食品标签怎么看、减盐减油原则。
    从已索引的国家权威膳食指南与慢病食养指南中检索相关片段，返回带【出处文件名】的依据，
    供你（小膳管家）给出可溯源的健康护栏建议。
    规则：
    ① 涉及健康/忌口/标签的问题必须先调用本工具再给结论，不要凭记忆编造；
    ② 只返回检索到的权威片段与 source 文件名，不臆造；
    ③ 检索为空时如实说明"未在权威指南中检索到"，不要硬凑。"""
    try:
        from rag.retriever import search as _rag_search
    except Exception as exc:
        return json.dumps(
            {"query": query, "found": False,
             "error": f"RAG 模块不可用：{exc}"},
            ensure_ascii=False,
        )
    try:
        result = _rag_search(query, n_results=top_k)
    except Exception as exc:
        return json.dumps(
            {"query": query, "found": False, "error": str(exc)},
            ensure_ascii=False,
        )
    if not getattr(result, "found", False):
        return json.dumps(
            {"query": query, "found": False, "hits": [],
             "note": "未在权威指南中检索到相关片段"},
            ensure_ascii=False,
        )
    items = []
    for h in result.hits:
        items.append({
            "source": h.source,
            "section": h.section,
            "distance": round(float(getattr(h, "distance", 0.0)), 4),
            "excerpt": h.text[:200],
            "metadata": {
                "category": h.metadata.get("category", ""),
                "anchor": h.metadata.get("anchor", ""),
                "doc": h.metadata.get("doc", ""),
                "content_type": h.metadata.get("content_type", ""),
            },
            "text": h.text[:600],
        })
    return json.dumps(
        {"query": query, "found": True, "count": len(items),
         "hits": items},
        ensure_ascii=False,
    )


# 所有工具的列表（交给 LLM 绑定 + ToolNode 统一执行）
tools = [web_search, get_file, nearby_food, calorie_lookup, exercise_equiv, nutrition_kb_search]#工具列表后面都会调用
