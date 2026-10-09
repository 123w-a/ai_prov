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

import base64      # 把生成图交给视觉模型做质量验收
import os          # 读环境变量
import json        # 本地菜品图缓存读写
import time        # 缓存文件名时间戳
import uuid        # 缓存文件名防重
import queue       # 外部调用超时后不等待后台线程
import threading   # 用守护线程包装可能卡住的 SDK 调用
import requests    # 下载通义万相返回的临时图片 URL
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

from infrastructure.model_name import extract_message_text, get_vision_llm

load_dotenv()      # 加载 .env（DASHSCOPE_API_KEY 等）

from .oss import upload_to_oss  # 把图片字节上传到自家 OSS，返回持久公网 URL

# ============================ 1. 配置（全部来自 .env） ============================ #
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")   # 通义万相 / 阿里云百炼 API Key（留空则功能降级）
WANX_MODEL = os.getenv("WANX_MODEL", "wanx2.1-t2i-turbo")  # 默认 turbo：稳定、快速；需要更精致再显式切 plus
WANX_SIZE = os.getenv("WANX_SIZE", "1024*1024")            # 生成分辨率
WANX_TIMEOUT_S = int(os.getenv("WANX_TIMEOUT_S", "20"))    # 单次生图超时；改用 plus 时可显式调大
IMAGE_QUALITY_ENABLED = os.getenv("IMAGE_QUALITY_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
IMAGE_QUALITY_TIMEOUT_S = int(os.getenv("IMAGE_QUALITY_TIMEOUT_S", "8"))
IMAGE_GENERATION_BUDGET_S = int(os.getenv("IMAGE_GENERATION_BUDGET_S", "45"))
# 缓存文件路径：菜名 -> 自家 OSS URL，命中即秒出、零成本
CACHE_PATH = os.path.join("resources", "dish_image_cache.json")
CACHE_VERSION = "v4"

# ============================ 2. 提示词工程（最关键的一环） ============================ #
# 为什么需要模板：把「红烧肉」这种裸中文菜名直接丢给文生图，效果一般（容易出奇葩构图/暗调）。
# 经验模板：锁定「中式家常菜 + 真实商业美食摄影 + 食材形态可辨认」这一类稳定出好图的风格。
# 对特殊食材额外加形态约束，防止把金针菇画成鱿鱼圈、把肉片画成厚块等“菜名对了但东西不对”的问题。
DISH_IMAGE_PROMPT_TEMPLATE = (
    "真实商业美食摄影成品图，{dish}。第一优先级是让人一眼就想吃，必须像刚出锅、热气腾腾、"
    "色泽鲜亮、质地鲜嫩多汁的餐厅现做出品。"
    "先严格还原菜名里每种主食材的真实种类、切面和形态（丝、片、条、块、丁、整只等），"
    "主食材清晰可辨，不能替换成其他食材。{shape_guidance}"
    "菜名中的烹饪方法和标志性调味必须准确呈现，例如剁椒要有红亮辣椒碎、清蒸要清爽湿润、"
    "红烧要有红亮酱汁、糖醋要有明亮糖醋汁；不要把标志性调味画成无关的黄色油或干粉。{style_guidance}"
    "成品必须呈现烹饪后的熟化质感，不能像未经处理的生鲜食材拼盘。"
    "选择适合这道菜的干净餐盘，主体占画面约七成，四十五度微俯拍或接近平视，侧逆光形成柔和高光，"
    "清晰可见的细密热气，表面有自然的油润或汤汁高光但不过度油亮，食物表面纹理清楚，"
    "颜色自然、饱满、有层次，浅景深虚化背景，"
    "像口碑餐厅现做出品，真实、诱人、有食欲。8k高清写实。{strict_guidance}"
)
STRICT_GENERATION_GUIDANCE = (
    "这是纠错重生成：优先把食欲做出来，同时保证食材种类和形态绝对准确。"
    "必须体现菜名中的烹饪方法和标志性调味。"
    "如果不确定复杂摆盘，就采用简洁真实的家庭餐厅摆盘，不添加无关配菜，不改变主食材形状。"
)
# 反向提示词：规避 AI 生图常见的「畸形、水印、卡通感」等破绽，保证像真实成品照
WANX_NEGATIVE_PROMPT = (
    "低分辨率、模糊、畸变、食材不可辨认、错误食材、食材种类被替换、形态错误、"
    "多余元素、水印、文字、过度修饰、卡通、插画、CG感、塑料质感、蜡质感、"
    "颜色发灰、苍白、无油光、干瘪、干柴、水塌塌、没熟、冷盘感、食欲低、"
    "糊成一团、摆盘杂乱、廉价外卖感、夸张特效"
)
STRICT_NEGATIVE_PROMPT = (
    WANX_NEGATIVE_PROMPT
    + "、主体错误、食材比例失真、形状与菜名不符、画面脏乱、过饱和、油光发黑"
)

# 只对常见、容易“看错对象”的食材加精确形态约束；未命中的菜仍走通用真实摄影模板。
# 每项格式：(菜名关键词, 正向形态约束, 负向错误形态)
_DISH_SHAPE_RULES = (
    (
        ("金针菇",),
        "金针菇必须呈现大量细长、白色或浅米色的菌柄，顶端是细小圆菌盖，整簇纵向分布、根根分明，"
        "蒸熟后仍保持细长丝状，并自然铺开或微微散开；除菜名明确为卷类外，"
        "不要整把直立成圆柱花束，不要生鲜拼盘感。",
        "鱿鱼圈、杏鲍菇厚片、宽粉、宽面、短粗块、肉片、零散碎段、"
        "整把直立、圆柱状花束、未经蒸熟的生鲜外观",
    ),
    (
        ("杏鲍菇", "口蘑", "香菇", "蘑菇", "菌菇", "鸡腿菇"),
        "菌菇应呈现真实菌盖与菌柄结构，切面有自然纤维和厚度，不能画成肉块或面筋。",
        "肉块、面筋、塑料片、看不出菌盖和菌柄的方块",
    ),
    (
        ("粉丝", "粉条", "宽粉"),
        "粉丝或粉条必须呈细长、半透明、柔韧的条状，根根有分离感，不能画成整块胶状物。",
        "整块胶块、短粗块、塑料条、完全糊成一片",
    ),
    (
        ("豆腐",),
        "豆腐应呈规整的嫩白方块或厚片，边缘自然，表面细嫩，不能画成肉块或奶酪。",
        "肉块、奶酪、碎渣、不明胶块",
    ),
    (
        (
            "鲈鱼", "鱼头", "鱼片", "鱼块", "鱼排", "鳕鱼", "带鱼", "鲫鱼",
            "草鱼", "黑鱼", "龙利鱼", "三文鱼", "清蒸鱼", "红烧鱼", "糖醋鱼",
            "酸菜鱼", "水煮鱼", "烤鱼", "鱼汤", "鱼肉",
        ),
        "鱼应保留明确的鱼皮、鱼肉纹理和鱼骨/鱼头结构；鱼片要呈自然薄片，不能画成鸡肉或肥肉。",
        "鸡胸块、猪肥肉、鱼形玩具、看不出鱼肉纹理的白色团块",
    ),
    (
        ("虾", "虾仁", "河虾", "基围虾"),
        "虾应呈现自然弯曲的虾身、节段和尾壳特征，虾仁要保持虾的轮廓，不能画成椭圆肉丸。",
        "肉丸、鱼丸、鸡块、没有虾节段的白色椭圆体",
    ),
    (
        ("牛肉", "羊肉", "肉片", "肉丝", "鸡胸", "鸡腿", "鸡肉"),
        "肉类要按菜名呈现正确形态：片要薄、丝要细长、块要有真实肌肉纤维，不能把不同肉类混画。",
        "橡胶块、整团肉泥、肉类种类混淆、没有纤维纹理的塑料块",
    ),
    (
        ("鸡蛋", "蛋花", "蛋羹", "蒸蛋"),
        "鸡蛋应呈现自然的凝固、软嫩蛋花或蛋羹结构，颜色是温和蛋黄/蛋白色，不能画成奶油或芝士。",
        "奶油、芝士、塑料模型、过硬的蛋块、颜色失真",
    ),
    (
        ("西兰花", "花菜"),
        "西兰花或花菜要保留明显的花球颗粒、短茎和自然绿色，不能画成普通青菜叶或碎末。",
        "普通叶菜、碎草、绿色糊状物",
    ),
    (
        ("茄子",),
        "茄子应呈现深紫或浅紫色外皮、软嫩茄肉和自然条块形态，不能画成肉片或紫薯。",
        "肉片、紫薯、紫色糊状物、黑色焦块",
    ),
    (
        ("土豆", "马铃薯"),
        "土豆要呈现自然的淡黄色块、片或丝，边缘有淀粉质感，不能画成年糕或奶酪。",
        "年糕、奶酪、塑料块、看不清土豆切面的白色块",
    ),
    (
        ("番茄", "西红柿"),
        "番茄应呈自然红色果肉、番茄汁和软嫩切块，不能画成草莓、辣椒或红色酱团。",
        "草莓、辣椒、红色塑料块、完全看不出果肉的酱",
    ),
    (
        (
            "面条", "米线", "米粉", "土豆粉", "炒面", "汤面", "拌面", "拉面",
            "刀削面", "手擀面", "凉面", "挂面", "方便面", "阳春面", "牛肉面",
            "意大利面", "意面",
        ),
        "面条要呈细长、柔软、带自然弯曲的面条结构，不能画成粉丝、宽粉或一团面糊。",
        "宽粉、粉丝、面糊、胶状块、看不出面条的单块面饼",
    ),
    (
        ("米饭", "粥", "蒸饭"),
        "米饭或粥要呈现自然的颗粒/米汤质感，颗粒之间有辨识度，不能画成土豆泥或奶油。",
        "土豆泥、奶油、塑料颗粒、看不出米粒的糊",
    ),
)

_DISH_STYLE_RULES = (
    (
        ("剁椒",),
        "剁椒必须红亮、湿润、颗粒清晰，覆盖在食材表面；盘底要有鲜亮红油或蒸汁反光，"
        "配少量翠绿葱花提色，整体颜色红亮但不脏。",
        "剁椒发黑、发黄、干成粉末、看不见剁椒颗粒、只有清汤没有红亮汁水",
    ),
    (
        ("红烧", "卤", "酱烧"),
        "酱汁要红亮浓稠、均匀包裹食材，表面有自然油亮反光，不能发黑发苦。",
        "酱汁发黑、干巴巴、颜色灰暗、食材没有挂汁",
    ),
    (
        ("糖醋", "酸甜"),
        "糖醋汁要明亮通透、有自然黏度和挂汁感，颜色鲜艳但不过度化学感。",
        "糖醋汁浑浊、发暗、像糖浆、食材干瘪不挂汁",
    ),
    (
        ("蒜蓉",),
        "蒜蓉要呈湿润细碎状并带自然油光，不能变成干粉或大块生蒜。",
        "蒜蓉干粉、发黄发黑、辛辣生蒜块",
    ),
    (
        ("清蒸", "蒸"),
        "蒸菜要有刚揭锅的细密热气和清亮汁水，食材湿润饱满、不过度油亮。",
        "干柴、水塌塌、没有热气、蒸汁浑浊发黑",
    ),
    (
        ("汤", "羹", "煲", "炖"),
        "汤汁要清亮或浓郁且符合菜名，表面有自然热气和少量油珠，食材浸润但不浑浊。",
        "汤水灰暗、浑浊、像剩菜、食材漂浮干硬",
    ),
)

_RULE_KEYWORD_BLOCKERS = {
    "鸡腿": ("鸡腿菇",),
}


def _matched_shape_rules(dish_name: str):
    """返回菜名命中的食材形态规则，最多合并三条，避免提示词过长稀释重点。"""
    text = str(dish_name or "").strip().casefold()
    matched = []
    for keywords, positive, negative in _DISH_SHAPE_RULES:
        if any(
            keyword.casefold() in text
            and not any(
                blocker.casefold() in text
                for blocker in _RULE_KEYWORD_BLOCKERS.get(keyword, ())
            )
            for keyword in keywords
        ):
            matched.append((positive, negative))
            if len(matched) >= 3:
                break
    return matched


def _matched_style_rules(dish_name: str):
    """返回菜名命中的烹饪风格约束，最多合并两条。"""
    text = str(dish_name or "").strip().casefold()
    matched = []
    for keywords, positive, negative in _DISH_STYLE_RULES:
        if any(keyword.casefold() in text for keyword in keywords):
            matched.append((positive, negative))
            if len(matched) >= 2:
                break
    return matched


def _clean_retry_problems(problems):
    """收敛视觉验收问题，避免长文本或换行污染第二次生成提示词。"""
    if not isinstance(problems, (list, tuple)):
        problems = [problems]
    cleaned = []
    for item in problems:
        text = " ".join(str(item or "").split()).strip("。；;，, ")
        if text and text not in cleaned:
            cleaned.append(text[:80])
        if len(cleaned) >= 3:
            break
    return cleaned


def build_image_prompt(
    dish_name: str,
    *,
    strict: bool = False,
    retry_problems=None,
) -> str:
    """把菜名套进固定风格模板，产出稳定出好图的生图提示词。

    Args:
        dish_name: 菜品名（如「红烧肉」），通常来自 web_search 的原始查询（已去掉「美食 成品图」后缀）。
        strict: 首次质量验收未通过时，使用更强调形态纠错的版本。
        retry_problems: 首图验收发现的具体问题，用于第二次生成针对性纠错。
    Returns:
        拼好的中文提示词字符串。
    """
    cleaned = str(dish_name or "").strip()
    matched = _matched_shape_rules(cleaned)
    shape_guidance = "".join(item[0] for item in matched)
    if shape_guidance:
        shape_guidance = "食材形态硬约束：" + shape_guidance
    style_guidance = "".join(item[0] for item in _matched_style_rules(cleaned))
    if style_guidance:
        style_guidance = "烹饪风格与食欲硬约束：" + style_guidance
    strict_guidance = STRICT_GENERATION_GUIDANCE if strict else ""
    problems = _clean_retry_problems(retry_problems)
    if strict and problems:
        strict_guidance += "上一张图的具体问题：" + "；".join(problems) + "。必须避免重复。"
    return DISH_IMAGE_PROMPT_TEMPLATE.format(
        dish=cleaned,
        shape_guidance=shape_guidance,
        style_guidance=style_guidance,
        strict_guidance=strict_guidance,
    )


def build_negative_prompt(
    dish_name: str,
    *,
    strict: bool = False,
    retry_problems=None,
) -> str:
    """按菜名追加对应的“错误形态”负向词，减少食材被画成别的东西。"""
    matched = _matched_shape_rules(dish_name)
    extras = []
    for _, negative in matched:
        if negative and negative not in extras:
            extras.append(negative)
    for _, negative in _matched_style_rules(dish_name):
        if negative and negative not in extras:
            extras.append(negative)
    problems = _clean_retry_problems(retry_problems)
    if strict and problems:
        extras.append("上一图问题：" + "；".join(problems))
    base = STRICT_NEGATIVE_PROMPT if strict else WANX_NEGATIVE_PROMPT
    return base + ("、" + "、".join(extras) if extras else "")


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


def _cache_key(dish: str) -> str:
    """给缓存键加版本号。

    提示词和生图模型升级后，旧键里的图片可能已经不符合当前质量标准；版本化后
    会自然绕过旧图重新生成，同时保留旧数据，避免误删历史资源。
    """
    return f"{CACHE_VERSION}:{_normalize(dish)}"


def _find_cached_url(cache: dict, dish: str):
    """先读当前版本，再兼容旧版缓存，避免升级键名后已有图片集体失效。"""
    normalized = _normalize(dish)
    return cache.get(_cache_key(dish)) or cache.get(normalized)


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
    return _find_cached_url(_load_cache(), dish_name)


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


_IMAGE_QUALITY_LLM = None


def _call_with_timeout(func, timeout_s, *args, **kwargs):
    """在守护线程中执行外部调用，超时即抛错，不等待线程结束。"""
    timeout_s = max(0.01, float(timeout_s))
    result_queue = queue.Queue(maxsize=1)

    def _runner():
        try:
            result_queue.put((True, func(*args, **kwargs)))
        except Exception as exc:
            result_queue.put((False, exc))

    worker = threading.Thread(
        target=_runner,
        daemon=True,
        name=f"image-timeout:{getattr(func, '__name__', 'call')}",
    )
    worker.start()
    try:
        succeeded, value = result_queue.get(timeout=timeout_s)
    except queue.Empty:
        raise TimeoutError(
            f"{getattr(func, '__name__', 'call')} timed out after {timeout_s:g}s"
        ) from None
    if succeeded:
        return value
    raise value


def _request_wanx_image(prompt: str, negative_prompt: str, timeout_s=None):
    """调用一次通义万相并返回临时图片 URL；异常/超时统一返回 None。"""
    try:
        import dashscope
        from dashscope import ImageSynthesis
        from http import HTTPStatus
    except ImportError:
        print("[image_gen] 未安装 dashscope，跳过 AI 生图（pip install dashscope 即可启用）")
        return None

    dashscope.api_key = DASHSCOPE_API_KEY
    timeout_s = WANX_TIMEOUT_S if timeout_s is None else max(0.01, float(timeout_s))
    try:
        result = _call_with_timeout(
            ImageSynthesis.call,
            timeout_s,
            model=WANX_MODEL,
            prompt=prompt,
            negative_prompt=negative_prompt,
            n=1,
            size=WANX_SIZE,
        )
    except TimeoutError:
        print(f"[image_gen] 通义万相生成超时（>{timeout_s}s），放弃本次生成")
        return None
    except Exception as e:
        print(f"[image_gen] 通义万相调用异常：{e}")
        return None

    if getattr(result, "status_code", None) != HTTPStatus.OK:
        print(
            f"[image_gen] 通义万相返回非成功状态：{getattr(result, 'status_code', '?')} "
            f"{getattr(result, 'message', '')}"
        )
        return None
    output = getattr(result, "output", None)
    results = output.get("results") if isinstance(output, dict) else getattr(output, "results", None)
    if not results:
        print("[image_gen] 通义万相未返回图片结果")
        return None
    first = results[0]
    return first.get("url") if isinstance(first, dict) else getattr(first, "url", None)


def _download_generated_image(gen_url: str):
    """下载通义万相临时图并做图片头校验，返回 (bytes, content_type) 或 None。"""
    try:
        resp = requests.get(gen_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200 or not _looks_like_image(resp.content):
            print("[image_gen] 下载生成图失败或内容非图片，放弃")
            return None
        return resp.content, resp.headers.get("Content-Type", "image/jpeg")
    except Exception as e:
        print(f"[image_gen] 下载生成图异常：{e}")
        return None


def _parse_quality_assessment(text: str):
    """解析视觉模型返回的 JSON；模型偶尔包 markdown 时也尽量兼容。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    def _as_match(value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().casefold() not in {
            "false", "no", "0", "否", "不是", "不匹配", "不一致",
        }

    try:
        appeal = float(data.get("appeal_score", 5))
    except Exception:
        appeal = 5.0
    appeal = max(1.0, min(5.0, appeal))
    problems = data.get("problems")
    if not isinstance(problems, list):
        problems = [str(problems)] if problems else []
    return {
        "dish_name_match": _as_match(data.get("dish_name_match", True)),
        "ingredient_shape_match": _as_match(data.get("ingredient_shape_match", True)),
        "appeal_score": appeal,
        "problems": [str(item) for item in problems if str(item).strip()],
    }


def _audit_generated_dish_image(dish_name: str, image_bytes: bytes, content_type: str):
    """用视觉模型验收生成图；任何不确定结果都放行首图，不阻塞稳定出图。"""
    if not IMAGE_QUALITY_ENABLED:
        print("[image_gen] 视觉验收已关闭，直接使用生成图")
        return None
    global _IMAGE_QUALITY_LLM
    try:
        if _IMAGE_QUALITY_LLM is None:
            _IMAGE_QUALITY_LLM = get_vision_llm(
                temperature=0,
                max_tokens=180,
                timeout=IMAGE_QUALITY_TIMEOUT_S,
            )
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:{content_type};base64,{image_base64}"
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "你是严格的菜品成品图质量审核器。"
                        f"目标菜名：{dish_name}。"
                        "判断图中的主食材种类和切面形态是否与菜名一致，并评估真实诱人程度。"
                        "只输出一个 JSON 对象，不要 markdown，不要解释："
                        '{"dish_name_match": true/false, "ingredient_shape_match": true/false, '
                        '"appeal_score": 1-5, "problems": ["问题"]}'
                        "dish_name_match 表示是否是目标菜且没有换成别的菜；"
                        "ingredient_shape_match 表示主食材形态是否正确（例如金针菇必须是细长菌柄和小菌盖、成簇根根分明，"
                        "不能是鱿鱼圈、杏鲍菇厚片或宽粉）；"
                        "appeal_score 中 5 是非常有食欲的真实餐厅出品，1 是明显失真或令人不想吃。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        )
        response = _IMAGE_QUALITY_LLM.invoke([message])
        raw_text = extract_message_text(response)
        assessment = _parse_quality_assessment(raw_text)
        if assessment:
            print(
                f"[image_gen] 视觉验收 {dish_name}："
                f"菜名{'通过' if assessment['dish_name_match'] else '失败'}，"
                f"形态{'通过' if assessment['ingredient_shape_match'] else '失败'}，"
                f"食欲 {assessment['appeal_score']:.1f}/5"
            )
        else:
            preview = " ".join(str(raw_text or "").split())[:300]
            print(f"[image_gen] 视觉验收返回无法解析，按首图兜底，不重生成：{preview!r}")
            return None
        return assessment
    except Exception as exc:
        # 验收是提质增强而不是新的单点故障：模型不可用时仍然返回首张图，保持原有可出图能力。
        print(f"[image_gen] 视觉验收不可用，放行首张生成图：{exc}")
        return None


def _quality_passed(assessment) -> bool:
    """质量门槛：只把明确的菜名/形态错误当作重试信号。"""
    if assessment is None:
        return True
    return bool(
        assessment.get("dish_name_match", True)
        and assessment.get("ingredient_shape_match", True)
    )


def _assessment_rank(attempt) -> tuple:
    assessment = attempt.get("assessment")
    if assessment is None:
        dish_ok, shape_ok, appeal = True, True, 5.0
    else:
        dish_ok = bool(assessment.get("dish_name_match", True))
        shape_ok = bool(assessment.get("ingredient_shape_match", True))
        appeal = float(assessment.get("appeal_score", 5))
    return int(dish_ok) * 100 + int(shape_ok) * 20 + appeal - int(
        bool(assessment and assessment.get("parse_failed"))
    )


def _select_generated_attempt(attempts: list):
    """从首图和纠错图里选最佳一张；都未通过质量门时仍回退首图，保证稳定出图。"""
    usable = []
    for index, attempt in enumerate(attempts):
        assessment = attempt.get("assessment")
        if assessment is None or assessment.get("dish_name_match", True):
            usable.append((_assessment_rank(attempt), index, attempt))
    if not usable:
        return attempts[0] if attempts else None
    return max(usable, key=lambda item: (item[0], item[1]))[2]


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
    cache_key = _cache_key(dish_name)
    cache = _load_cache()
    cached_url = _find_cached_url(cache, dish_name)
    if cached_url:
        print(f"[image_gen] 命中缓存，秒出图（零成本）：{cache_key}")
        return cached_url

    # --- 1. 无密钥直接降级（不打断主流程） ---
    if not DASHSCOPE_API_KEY:
        print("[image_gen] 未配置 DASHSCOPE_API_KEY，跳过 AI 生图，回退到「无图」")
        return None

    # --- 2. 生成首图；仅有明确质量问题时，在总预算内最多纠错重生成一次 ---
    generation_started = time.monotonic()
    attempts = []
    retry_problems = []
    for attempt_no, strict in enumerate((False, True), start=1):
        attempt_timeout = WANX_TIMEOUT_S
        if attempt_no > 1:
            remaining = IMAGE_GENERATION_BUDGET_S - (time.monotonic() - generation_started)
            if remaining <= 0:
                print("[image_gen] 纠错重生成预算已耗尽，使用当前已生成图片")
                break
            attempt_timeout = min(WANX_TIMEOUT_S, max(1, int(remaining)))
        prompt = build_image_prompt(
            dish_name,
            strict=strict,
            retry_problems=retry_problems,
        )
        negative_prompt = build_negative_prompt(
            dish_name,
            strict=strict,
            retry_problems=retry_problems,
        )
        print(
            f"[image_gen] 调用通义万相生成菜品图：{cache_key}（模型 {WANX_MODEL}，"
            f"{'严格纠错' if strict else '首图'}，超时 {attempt_timeout}s）"
        )
        gen_url = _request_wanx_image(prompt, negative_prompt, timeout_s=attempt_timeout)
        if not gen_url:
            break
        downloaded = _download_generated_image(gen_url)
        if not downloaded:
            continue
        img_bytes, content_type = downloaded
        assessment = _audit_generated_dish_image(dish_name, img_bytes, content_type)
        attempts.append(
            {
                "image_bytes": img_bytes,
                "content_type": content_type,
                "assessment": assessment,
            }
        )
        if _quality_passed(assessment):
            break
        if attempt_no == 1:
            retry_problems = (assessment or {}).get("problems") or []
            print("[image_gen] 首图未通过视觉验收，启用严格提示词纠错重生成一次")

    chosen = _select_generated_attempt(attempts)
    if not chosen:
        print(f"[image_gen] 未取得可下载的生成图：{cache_key}")
        return None
    img_bytes = chosen["image_bytes"]
    content_type = chosen["content_type"]

    # --- 3. 上传自家 OSS，得到持久公网 URL，并写入缓存 ---
    # 显式加 15s 超时（OSS SDK 默认超时很长，外部接口抽风时不能拖累整轮）
    try:
        oss_url = _call_with_timeout(upload_to_oss, 15, img_bytes, content_type)
        cache[cache_key] = oss_url
        _save_cache(cache)
        chosen_assessment = chosen.get("assessment")
        quality_note = ""
        if chosen_assessment:
            quality_note = (
                f"（形态{'通过' if chosen_assessment.get('ingredient_shape_match') else '未通过'}，"
                f"食欲 {float(chosen_assessment.get('appeal_score', 0)):.1f}/5）"
            )
        print(f"[image_gen] 生成并缓存成功{quality_note}：{cache_key} -> {oss_url}")
        return oss_url
    except TimeoutError:
        print("[image_gen] 上传 OSS 超时（>15s），回退无图")
        return None
    except Exception as e:
        print(f"[image_gen] 上传 OSS 失败：{e}")
        return None
