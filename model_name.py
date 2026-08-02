from configs import MODEL_CONFIGS  # 传入模型参数（已从 .env 加载）
from langchain_openai import ChatOpenAI  # 创建 LangChain 的 OpenAI 兼容对象


def get_langchain_llm(
    provider,
    temperature=0.7,
    max_tokens=1024,
    api_key=None,
    base_url=None,
    model_name=None,
):
    """统一创建 LangChain ChatOpenAI 实例。

    双兜底配置原则（工程化亮点）：
    1. 函数参数优先：调用方传入 api_key/base_url/model_name 时，直接覆盖 .env，
       方便本地调试临时切换模型/密钥，无需改 .env；
    2. 无参读 .env：不传参时，自动回退到 MODEL_CONFIGS（由 configs.py 从 .env 加载），
       部署时只维护环境变量即可。

    健壮性：
    - provider 不存在时抛 ValueError，避免 KeyError 暴露内部结构；
    - api_key 为空时提前报错，不把 None 传给 ChatOpenAI 导致更难懂的厂商错误；
    - max_tokens 为 None 时不传入，兼容不接受 null 的厂商 API。

    Args:
        provider: 模型服务商键名，如 "gpt" / "deepseek"，对应 MODEL_CONFIGS；
        temperature: 采样温度，结构化输出建议 0.1~0.2；
        max_tokens: 单次生成最大 token 上限；None 表示不限制；
        api_key: 可选覆盖 .env 的 API 密钥；
        base_url: 可选覆盖 .env 的代理/中转地址；
        model_name: 可选覆盖 .env 的模型名。

    Returns:
        ChatOpenAI 实例，已开启 streaming=True（供 LangGraph .stream() 逐 token 输出，
        .invoke() / .ainvoke() 仍可正常返回完整消息）。
    """
    if provider not in MODEL_CONFIGS:
        raise ValueError(
            f"不支持的模型服务商：{provider}。可用选项：{list(MODEL_CONFIGS.keys())}"
        )

    cfg = MODEL_CONFIGS[provider]

    # 双兜底：函数参数 > .env 配置
    final_api_key = api_key if api_key is not None else cfg["api_key"]
    final_base_url = base_url if base_url is not None else cfg["base_url"]
    final_model_name = model_name if model_name is not None else cfg["model_name"]

    if not final_api_key:
        raise ValueError(
            f"provider={provider} 缺少 api_key，请在 .env 中配置或在调用时传入 api_key 参数"
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
