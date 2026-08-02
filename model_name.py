import os  # 读取 CHEF_PROVIDER 环境变量（主脑模型选择）

from configs import MODEL_CONFIGS  # 传入模型参数（已从 .env 加载）
from langchain_openai import ChatOpenAI  # 创建 LangChain 的 OpenAI 兼容对象


# --------------------------------------------------------------------------- #
#  自适应 provider 解析：解决"默认用哪一个"的困惑
#  - 优先级：显式参数 > 环境变量 CHEF_PROVIDER > .env 中第一个可用的
#  - 指定的 provider 在 .env 没配 api_key 时，自动回退并明确告知用户实际用的是谁
# --------------------------------------------------------------------------- #
def _is_configured(provider):
    """provider 是否在 .env 里配了有效 api_key（可用）。"""
    cfg = MODEL_CONFIGS.get(provider)
    return bool(cfg and cfg.get("api_key"))


def _first_configured():
    """自动发现 .env 中第一个可用的 provider（按 MODEL_CONFIGS 写入顺序）。"""
    for name in MODEL_CONFIGS:
        if _is_configured(name):
            return name
    return None


def resolve_provider(preferred=None):
    """决定最终用哪个 provider，并明确告知（解决"默认用哪个"问题）。

    返回实际可用的 provider 名；若全部未配置则抛 ValueError。
    当发生回退时打印一行提示，让用户清楚当前跑的是哪个模型。
    """
    # 1. 显式指定且 .env 里配了 -> 直接用
    if preferred and _is_configured(preferred):
        return preferred
    # 2. 环境变量 CHEF_PROVIDER
    env_p = os.getenv("CHEF_PROVIDER")
    if env_p and _is_configured(env_p):
        if preferred and preferred != env_p:
            print(
                f"[model] 指定 provider={preferred!r} 不可用，已改用环境变量 "
                f"CHEF_PROVIDER={env_p!r}"
            )
        return env_p
    # 3. 自动发现 .env 中第一个可用的
    fallback = _first_configured()
    if fallback:
        if preferred and preferred != fallback:
            print(
                f"[model] 指定 provider={preferred!r} 未配置 api_key，"
                f"已自动回退到 {fallback!r}"
            )
        return fallback
    raise ValueError(
        "没有任何可用的模型配置：请在 .env 至少配置一个 provider 的 *_API_KEY"
        f"（可选：{list(MODEL_CONFIGS.keys())}）"
    )


def get_langchain_llm(
    provider=None,
    temperature=0.7,
    max_tokens=1024,
    api_key=None,
    base_url=None,
    model_name=None,
):
    """统一创建 LangChain ChatOpenAI 实例。

    三层 provider 解析（详见 resolve_provider）：
      显式参数 > 环境变量 CHEF_PROVIDER > .env 第一个可用配置。
    指定模型在 .env 没配时会自动回退并明确告知，绝不静默崩溃。

    双兜底配置原则（工程化亮点）：
    1. 函数参数优先：调用方传入 api_key/base_url/model_name 时，直接覆盖 .env，
       方便本地调试临时切换模型/密钥，无需改 .env；
    2. 无参读 .env：不传参时，自动回退到 MODEL_CONFIGS（由 configs.py 从 .env 加载），
       部署时只维护环境变量即可。

    健壮性：
    - provider 解析失败（全部未配置）抛 ValueError，给出清晰指引；
    - api_key 为空时提前报错，不把 None 传给 ChatOpenAI 导致更难懂的厂商错误；
    - max_tokens 为 None 时不传入，兼容不接受 null 的厂商 API。

    Args:
        provider: 模型服务商键名，如 "gpt" / "deepseek" / "qwen"，对应 MODEL_CONFIGS；
                  为 None 时按 resolve_provider 自动解析；
        temperature: 采样温度，结构化输出建议 0.1~0.2；
        max_tokens: 单次生成最大 token 上限；None 表示不限制；
        api_key: 可选覆盖 .env 的 API 密钥；
        base_url: 可选覆盖 .env 的代理/中转地址；
        model_name: 可选覆盖 .env 的模型名。

    Returns:
        ChatOpenAI 实例，已开启 streaming=True（供 LangGraph .stream() 逐 token 输出，
        .invoke() / .ainvoke() 仍可正常返回完整消息）。
    """
    chosen = resolve_provider(provider)
    cfg = MODEL_CONFIGS[chosen]

    # 双兜底：函数参数 > .env 配置
    final_api_key = api_key if api_key is not None else cfg["api_key"]
    final_base_url = base_url if base_url is not None else cfg["base_url"]
    final_model_name = model_name if model_name is not None else cfg["model_name"]

    if not final_api_key:
        raise ValueError(
            f"provider={chosen} 缺少 api_key，请在 .env 中配置或在调用时传入 api_key 参数"
        )

    kwargs = {
        "model": final_model_name,
        "api_key": final_api_key,
        "base_url": final_base_url,
        "temperature": temperature,
        "streaming": True,  # 开启流式：让 LangGraph 的 .stream() 能逐 token 吐出最终回答
    }
    # max_tokens=None 时不传入，避免部分厂商 API 因接收 null 而报错
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    llm = ChatOpenAI(**kwargs)
    return llm
