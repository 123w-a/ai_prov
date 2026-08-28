# image_gen.py：AI 文生图兜底模块（通义万相 / Wanx）
# ---------------------------------------------------------------------------
# 设计定位：
#   - 这是「搜图失败 → 自动生成一张菜品示意图」的兜底实现（对应文档 7.4 方案 B）。
#   - 搜索（web_search）拿不到可靠成品图时，由本模块根据「菜名 + 固定风格模板」
#     调用通义万相生成一张菜品图，上传到自家 OSS 后返回公网 URL。
#   - 生成的图一律标记为「AI 生成示意图」，绝不伪装成真实成品照（透明标注是亮点）。
#   - 生成的 OSS URL 按菜名缓存到本地 JSON（对应文档 7.4 方案 D），
#     家常菜被反复请求时直接命中缓存、秒出图、零成本。
#
# 工程化要点（务必守住）：
#   1. 无 DASHSCOPE_API_KEY 时，generate_dish_image 直接返回 None，绝不抛异常、不阻断主流程；
#   2. dashscope 采用「懒导入」：即使没装这个包，本模块被 import 也不报错，仅功能降级；
#   3. 任何一步（生成/下载/上传/解析）失败都 try/except 兜住，返回 None，让上层回退到「没图」；
#   4. 生成结果先下载字节、再上传自家 OSS，最终只返回「持久可用的 OSS URL」，
#      不依赖通义万相返回的临时 URL（临时 URL 会过期，缓存毫无意义）。
# ---------------------------------------------------------------------------

import os          # 读环境变量
import json        # 本地菜品图缓存读写
import time        # 缓存文件名时间戳
import uuid        # 缓存文件名防重
import concurrent.futures  # 给万相调用加超时止损，避免抽风卡住整轮
import requests    # 下载通义万相返回的临时图片 URL
from dotenv import load_dotenv

load_dotenv()      # 加载 .env（DASHSCOPE_API_KEY 等）

from oss_utils import upload_to_oss  # 把图片字节上传到自家 OSS，返回持久公网 URL

# ============================ 1. 配置（全部来自 .env） ============================ #
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")   # 通义万相 / 阿里云百炼 API Key（留空则功能降级）
WANX_MODEL = os.getenv("WANX_MODEL", "wanx2.1-t2i-turbo")  # 文生图模型：turbo 快且便宜；plus 更精致
WANX_SIZE = os.getenv("WANX_SIZE", "1024*1024")            # 生成分辨率
# 缓存文件路径：菜名 -> 自家 OSS URL，命中即秒出、零成本
CACHE_PATH = os.path.join("resources", "dish_image_cache.json")

# ============================ 2. 提示词工程（最关键的一环） ============================ #
# 为什么需要模板：把「红烧肉」这种裸中文菜名直接丢给文生图，效果一般（容易出奇葩构图/暗调）。
# 经验模板：锁定「中式家常菜 + 白瓷盘 + 俯拍45度 + 自然光 + 食物摄影」这一类稳定出好图的风格，
# {dish} 占位符由具体菜名填充。该模板已写入文档 7.4 方案 B 示例，改动需同步文档。
DISH_IMAGE_PROMPT_TEMPLATE = (
    "中式家常菜，{dish}，盛放在白瓷盘中，俯拍45度视角，自然光照明，"
    "专业食物摄影风格，色泽诱人、构图干净、背景虚化，高清细节，真实感强"
)
# 反向提示词：规避 AI 生图常见的「畸形、水印、卡通感」等破绽，保证像真实成品照
WANX_NEGATIVE_PROMPT = "低分辨率、畸形、多余手指、水印、文字、过度修饰、卡通、插画风格、夸张特效"


def build_image_prompt(dish_name: str) -> str:
    """把菜名套进固定风格模板，产出稳定出好图的生图提示词。

    Args:
        dish_name: 菜品名（如「红烧肉」），通常来自 web_search 的原始查询（已去掉「美食 成品图」后缀）。
    Returns:
        拼好的中文提示词字符串。
    """
    return DISH_IMAGE_PROMPT_TEMPLATE.format(dish=dish_name.strip())


# ============================ 3. 本地缓存（方案 D：降本增效） ============================ #
def _normalize(dish: str) -> str:
    """菜名归一化：去掉搜索追加词与首尾空白，作为缓存键。

    例如「红烧肉 美食 成品图」→「红烧肉」，保证同一道菜在不同问法下命中同一份缓存。
    """
    d = dish.strip()
    for suffix in ("美食 成品图", "成品图", "美食"):
        if d.endswith(suffix):
            d = d[: -len(suffix)].strip()
    return d


def _load_cache() -> dict:
    """读取本地菜品图缓存；文件不存在/损坏则返回空 dict。"""
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def cached_dish_image(dish_name: str):
    """只查缓存不生成：供补图线程预算前置用。命中返回 OSS URL，未命中 None。"""
    if not dish_name or not dish_name.strip():
        return None
    return _load_cache().get(_normalize(dish_name))


def _save_cache(cache: dict) -> None:
    """把缓存落盘；失败仅打印，不影响主流程（缓存只是降本优化，非必须）。"""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[image_gen] 缓存写入失败（不影响出图）：{e}")


# ============================ 4. 字节校验（防通义万相返回非图片） ============================ #
def _looks_like_image(data: bytes) -> bool:
    """用文件头 magic bytes 校验，防止下载到 HTML 错误页之类的非图片内容。"""
    if len(data) < 12:
        return False
    return (
        data.startswith(b"\xff\xd8\xff")        # JPEG
        or data.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or data.startswith(b"GIF87a")
        or data.startswith(b"GIF89a")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


# ============================ 5. 核心：生成菜品示意图 ============================ #
def generate_dish_image(dish_name: str):
    """为某道菜生成「AI 示意图」并上传 OSS，返回持久公网 URL；任何失败都返回 None。

    调用顺序：缓存命中 → 直接返回 OSS URL（零成本）；
              未命中 → 调通义万相生成 → 下载字节 → 上传 OSS → 写缓存 → 返回 URL。

    透明标注约定：本函数只负责「出图 + 返回 URL」，是否标「AI 生成示意图」由
    agent_graph / 前端根据 image_ai_generated 字段决定（见 agent_schemas / frontend/web）。
    本函数绝不伪装这是真实成品照。

    Args:
        dish_name: 菜品名（建议传去掉搜索后缀的原始菜名）。
    Returns:
        str: 自家 OSS 公网 URL；无密钥 / 生成失败 / 上传失败 均返回 None。
    """
    # --- 0. 入参与缓存守卫 ---
    if not dish_name or not dish_name.strip():
        return None
    cache_key = _normalize(dish_name)
    cache = _load_cache()
    if cache_key in cache:
        print(f"[image_gen] 命中缓存，秒出图（零成本）：{cache_key}")
        return cache[cache_key]

    # --- 1. 无密钥直接降级（不打断主流程） ---
    if not DASHSCOPE_API_KEY:
        print("[image_gen] 未配置 DASHSCOPE_API_KEY，跳过 AI 生图，回退到「无图」")
        return None

    # --- 2. 懒导入 dashscope：没装包也不影响其它功能 ---
    try:
        import dashscope
        from dashscope import ImageSynthesis
        from http import HTTPStatus
    except ImportError:
        print("[image_gen] 未安装 dashscope，跳过 AI 生图（pip install dashscope 即可启用）")
        return None

    dashscope.api_key = DASHSCOPE_API_KEY
    prompt = build_image_prompt(dish_name)

    # --- 3. 调通义万相生成（异步任务，SDK 内部自动轮询等待结果） ---
    try:
        print(f"[image_gen] 调用通义万相生成菜品图：{cache_key}（模型 {WANX_MODEL}）")
        # 用线程池包裹生成调用，单图最多等 20s，超时直接放弃兜底（正常 8-15s 不误杀），
        # 避免万相抽风卡 20-30s 拖垮整轮回答。
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(
                ImageSynthesis.call,
                model=WANX_MODEL,
                prompt=prompt,
                negative_prompt=WANX_NEGATIVE_PROMPT,
                n=1,
                size=WANX_SIZE,
            )
            result = future.result(timeout=20)
    except concurrent.futures.TimeoutError:
        print("[image_gen] 通义万相生成超时（>20s），放弃兜底生图，回退无图")
        return None
    except Exception as e:
        print(f"[image_gen] 通义万相调用异常：{e}")
        return None

    # --- 4. 鲁棒解析返回结果（兼容 dict / 对象两种形态） ---
    if getattr(result, "status_code", None) != HTTPStatus.OK:
        print(f"[image_gen] 通义万相返回非成功状态：{getattr(result, 'status_code', '?')} "
              f"{getattr(result, 'message', '')}")
        return None
    output = getattr(result, "output", None)
    results = output.get("results") if isinstance(output, dict) else getattr(output, "results", None)
    if not results:
        print("[image_gen] 通义万相未返回图片结果")
        return None
    first = results[0]
    gen_url = first.get("url") if isinstance(first, dict) else getattr(first, "url", None)
    if not gen_url:
        print("[image_gen] 通义万相返回结果中缺少图片 URL")
        return None

    # --- 5. 下载生成图字节（通义万相的 URL 是临时的，必须落自家 OSS 才持久） ---
    try:
        resp = requests.get(gen_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200 or not _looks_like_image(resp.content):
            print("[image_gen] 下载生成图失败或内容非图片，放弃")
            return None
        img_bytes = resp.content
        content_type = resp.headers.get("Content-Type", "image/jpeg")
    except Exception as e:
        print(f"[image_gen] 下载生成图异常：{e}")
        return None

    # --- 6. 上传自家 OSS，得到持久公网 URL，并写入缓存 ---
    # 显式加 15s 超时（OSS SDK 默认超时很长，外部接口抽风时不能拖累整轮）
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _oss_ex:
            _oss_future = _oss_ex.submit(upload_to_oss, img_bytes, content_type)
            oss_url = _oss_future.result(timeout=15)
        cache[cache_key] = oss_url
        _save_cache(cache)
        print(f"[image_gen] 生成并缓存成功：{cache_key} -> {oss_url}")
        return oss_url
    except concurrent.futures.TimeoutError:
        print("[image_gen] 上传 OSS 超时（>15s），回退无图")
        return None
    except Exception as e:
        print(f"[image_gen] 上传 OSS 失败：{e}")
        return None
