import os  # 读取 CHEF_PROVIDER 环境变量（主脑模型选择）
import logging
import threading
import time

from configs import MODEL_CONFIGS, VISION_CONFIGS  # 传入模型参数（已从 .env 加载）
from langchain_core.language_models import LanguageModelInput
from langchain_deepseek import ChatDeepSeek  # DeepSeek 原生适配：保留 thinking/reasoning_content
from langchain_openai import ChatOpenAI  # 创建 LangChain 的 OpenAI 兼容对象


logger = logging.getLogger(__name__)


class PatchedChatDeepSeek(ChatDeepSeek):
    """DeepSeek thinking 多轮补丁：把上一轮 assistant 的 reasoning_content 原样回传。"""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        source_messages = self._convert_input(input_).to_messages()

        for index, message in enumerate(payload.get("messages") or []):
            if message.get("role") != "assistant":
                continue
            source = source_messages[index] if index < len(source_messages) else None
            reasoning = getattr(source, "additional_kwargs", {}).get("reasoning_content")
            # DeepSeek thinking 模式要求：带 tool_calls 的 assistant 历史必须
            # 携带 reasoning_content，哪怕没有思维链也要回传空字符串。
            if message.get("tool_calls"):
                message["reasoning_content"] = reasoning or ""

        return payload


# --------------------------------------------------------------------------- #
#  自适应 provider 解析：解决"默认用哪一个"的困惑
#  - 优先级：显式参数 > 环境变量 CHEF_PROVIDER > .env 中第一个可用的
#  - 指定的 provider 在 .env 没配 api_key 时，自动回退并明确告知用户实际用的是谁
# --------------------------------------------------------------------------- #
def _is_configured(provider):
    """provider 是否在 .env 里配了有效 api_key（可用）。"""
    cfg = MODEL_CONFIGS.get(provider)
    return bool(cfg and cfg.get("api_key"))# 判断是否配置了 api_key和是否有这个模型


def _first_configured():#当没传参的时候就使用第一个
    """自动发现 .env 中第一个可用的 provider（按 MODEL_CONFIGS 写入顺序）。"""
    for name in MODEL_CONFIGS:
        if _is_configured(name):
            return name
    return None

def resolve_provider(preferred=None):#在graph中找到用哪个模型中
    """决定最终用哪个 provider（收口到这一处，全项目统一）。

    优先级：显式参数  >  .env 的 CHEF_PROVIDER 开关  >  .env 中第一个可用的。
    返回实际可用的 provider 名；若全部未配置则抛 ValueError。
    当发生回退时打印一行提示，让用户清楚当前跑的是哪个模型。
    """
    # 1. 显式指定且 .env 里配了 -> 直接用
    if preferred and _is_configured(preferred):
        return preferred

    # 2. .env 的 CHEF_PROVIDER 开关（想用哪个写哪个，不需改代码）
    env_provider = os.getenv("CHEF_PROVIDER")
    if env_provider and _is_configured(env_provider):
        return env_provider

    # 3. 自动发现 .env 中第一个可用的（无需任何传参）
    fallback = _first_configured()#获取第一个可用的模型
    if fallback:
        if preferred and preferred != fallback:
            print(
                f"[model] 指定 provider={preferred!r} 未配置 api_key，"
                f"已自动回退到 {fallback!r}"
            )
        elif env_provider and env_provider != fallback:
            print(
                f"[model] CHEF_PROVIDER={env_provider!r} 未配置 api_key，"
                f"已自动回退到 {fallback!r}"
            )
        return fallback
    raise ValueError(
        "没有任何可用的模型配置：请在 .env 至少配置一个 provider 的 *_API_KEY"
        f"（可选：{list(MODEL_CONFIGS.keys())}）"
    )


def _build_llm(
    chosen,
    cfg,
    *,
    temperature=0.7,
    max_tokens=1024,
    api_key=None,
    base_url=None,
    model_name=None,
    timeout=None,
):
    """从已解析的 provider 配置构造模型，供文本和视觉链路共用。"""
    # 双兜底：函数参数 > .env 配置
    final_api_key = api_key if api_key is not None else cfg["api_key"]
    final_base_url = base_url if base_url is not None else cfg["base_url"]
    final_model_name = model_name if model_name is not None else cfg["model_name"]

    if not final_api_key:
        raise ValueError(
            f"provider={chosen} 缺少 api_key，请在 .env 中配置或在调用时传入 api_key 参数"
        )

    kwargs = {#关键字可变长度参数
        "model": final_model_name,
        "api_key": final_api_key,
        "base_url": final_base_url,
        "temperature": temperature,
        "streaming": True,  #意思是能流式但也不阻止invoken（）的阻塞式
        "timeout": timeout if timeout is not None else 75,  # 上游偶发黑洞(180s+无响应)，75s 剪断后由 max_retries 重试一次；短任务（视觉审核等）可传更小值
        "max_retries": 1,
    }
    # max_tokens=None 时不传入，避免部分厂商 API 因接收 null 而报错
    if max_tokens is not None:#如果传了参就传进去，没传参就是大模型去默认别写个null在这里
        kwargs["max_tokens"] = max_tokens

    # DeepSeek thinking 模型在工具调用后必须把 reasoning_content 原样回传。
    # 通用 ChatOpenAI 会丢弃该字段，第二轮请求直接 400；官方适配类会保留它。
    llm_class = PatchedChatDeepSeek if chosen == "deepseek" else ChatOpenAI
    llm = llm_class(**kwargs)
    return llm


def get_langchain_llm(# 创建 LangChain ChatOpenAI 实例贯彻到底
    provider=None,
    temperature=0.7,
    max_tokens=1024,
    api_key=None,
    base_url=None,
    model_name=None,
    timeout=None,
):
    chosen = resolve_provider(provider)#可能是用户传的可能是环境变量的
    cfg = MODEL_CONFIGS[chosen]#获取模型参数
    return _build_llm(
        chosen,
        cfg,
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        timeout=timeout,
    )


def get_summary_model_name(provider: str) -> str | None:
    """返回摘要任务专用模型名；DeepSeek 默认用稳定返回正文的 chat 模型。"""
    if provider != "deepseek":
        return None
    return os.getenv("SUMMARY_MODE_NAME") or "deepseek-chat"


def get_summary_llm(
    provider=None,
    temperature=0.3,
    max_tokens=1024,
    timeout=None,
):
    """创建摘要/历史压缩专用模型，避免 reasoning 模型把正文只写在思维链里。"""
    chosen = resolve_provider(provider)
    return get_langchain_llm(
        chosen,
        temperature=temperature,
        max_tokens=max_tokens,
        model_name=get_summary_model_name(chosen),
        timeout=timeout,
    )


def extract_message_text(response, *, allow_reasoning_fallback: bool = False) -> str:
    """从 LangChain 消息提取正文；仅在摘要类降级场景允许读取 reasoning_content。"""
    content = getattr(response, "content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        content = "".join(parts)
    text = str(content or "").strip()
    if text:
        return text
    if not allow_reasoning_fallback:
        return ""

    reasoning = (getattr(response, "additional_kwargs", {}) or {}).get("reasoning_content")
    text = str(reasoning or "").strip()
    if text:
        logger.warning(
            "模型返回空 content，摘要任务降级使用 reasoning_content；"
            "请检查 SUMMARY_MODE_NAME 是否指向稳定的正文模型"
        )
    return text


def get_vision_llm(
    temperature=0,
    max_tokens=1024,
    timeout=45,
    model_name=None,
):
    """创建专用视觉模型，绝不回退到纯文本主脑或中转 Provider。"""
    provider = os.getenv("VISION_PROVIDER", "qwen")
    cfg = VISION_CONFIGS.get(provider)
    if not cfg or not cfg.get("api_key"):
        raise ValueError(
            f"视觉 provider={provider!r} 未配置 API key；"
            "请配置 QWEN_API_KEY 或 DASHSCOPE_API_KEY"
        )
    final_model = model_name or os.getenv("VISION_MODEL_NAME") or None
    return _build_llm(
        provider,
        cfg,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        model_name=final_model,
    )


# ---- provider 健康与 failover（A 方案：主模型黑洞时整轮切备用重跑）----
_PROVIDER_COOLDOWN: dict = {}
_COOLDOWN_LOCK = threading.Lock()
_PROVIDER_COOLDOWN_SECONDS = 300  # 黑洞后 5 分钟内不撞同一家


def mark_provider_down(provider: str, seconds: int = _PROVIDER_COOLDOWN_SECONDS) -> None:
    """把刚发生超时/连接故障的 provider 打入冷却期。"""
    with _COOLDOWN_LOCK:
        _PROVIDER_COOLDOWN[provider] = time.time() + seconds


def _provider_in_cooldown(provider: str) -> bool:
    with _COOLDOWN_LOCK:
        until = _PROVIDER_COOLDOWN.get(provider, 0)
    return time.time() < until


def pick_fallback_provider(exclude: str | None = None) -> str | None:
    """返回一个不在冷却期且已配置 key 的备用 provider；没有则 None。"""
    for name in MODEL_CONFIGS:
        if name == exclude or _provider_in_cooldown(name):
            continue
        if _is_configured(name):
            return name
    return None


def is_provider_failure(exc: BaseException) -> bool:
    """判定异常是否为上游模型超时/连接类故障（值得切换 provider 重试）。"""
    name = type(exc).__name__
    if name in {"APITimeoutError", "APIConnectionError", "TimeoutError", "ConnectionError"}:
        return True
    text = str(exc).lower()
    if any(frag in text for frag in ("timed out", "timeout", "connection", "upstream", "bad gateway", "service unavailable", "internal server error")):
        return True
    return " 5" in text and "server error" in text or text.startswith("5") and "error" in text
