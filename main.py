import os  # 注入主脑模型选择到环境变量
import mimetypes  # 区分 JPG / PNG 等 MIME 类型
from langchain_core.messages import HumanMessage, AIMessageChunk  # 用户消息类 + 流式增量块类型

# 主脑模型选择：想换模型改这里（"gpt" / "deepseek" / 未来 "qwen"），
# 需在 .env 配好对应 *_API_KEY；若没配，model_name 会自动回退到 .env 第一个可用模型并告知。
PROVIDER = "gpt"
# 在 import agent 之前注入，让 agent_graph 构建时读到用户指定的主脑模型
os.environ["CHEF_PROVIDER"] = PROVIDER

from agent import agent  # 调用写好的 LangGraph Agent
from oss_utils import upload_to_oss  # 把图片上传到 OSS 并返回公网 URL


def image_to_oss_url(image_path):  # 本地图片 -> OSS 公网 URL
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/jpeg"
    with open(image_path, "rb") as image_file:
        image_bytes = image_file.read()
    return upload_to_oss(image_bytes, mime_type)


def image_bytes_to_oss_url(image_bytes, mime_type="image/jpeg"):
    """直接把图片 bytes 上传到 OSS 并返回可访问 URL（无需先落本地磁盘）。"""
    if mime_type is None:
        mime_type = "image/jpeg"
    return upload_to_oss(image_bytes, mime_type)


def build_human_message(text, image_url=None):
    """统一的图文消息构造：有图就图文混排，没图就纯文本。
    所有 ask_*/stream_* 都复用它，消除 HumanMessage 重复拼装。"""
    if image_url:
        return HumanMessage(
            content=[
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        )
    return HumanMessage(content=text)


# --------------------------------------------------------------------------- #
#  非流式版本：agent.ainvoke 一次性跑完整状态机（含 structure_answer 结构化节点），
#  取最后一条消息即 ChefAnswer JSON。流式/非流式复用同一张图、同一套结构化链路。
# --------------------------------------------------------------------------- #
async def ask_agent(message, session_id):
    """非流式核心：直接 ainvoke 拿完整回答字符串（含结构化 JSON）。"""
    config = {"configurable": {"thread_id": session_id}}
    state = await agent.ainvoke({"messages": [message]}, config=config)
    return state["messages"][-1].content


async def ask_agent_with_text(text, session_id):
    return await ask_agent(build_human_message(text), session_id)


async def ask_agent_with_image_url(image_url, text, session_id):
    return await ask_agent(build_human_message(text, image_url), session_id)


async def ask_agent_with_image(image_path, text, session_id):
    return await ask_agent_with_image_url(image_to_oss_url(image_path), text, session_id)


# --------------------------------------------------------------------------- #
#  流式版本：agent.stream(stream_mode="messages") 逐 token 吐出，只过滤 LLM 增量。
#  图拓扑、工具、断点、压缩、结构化收尾逻辑完全不动。
#
#  两段式输出（LCEL 重构后）：每次 yield 一个 (kind, content) 元组——
#    ("token", 文字)  ：chef_think 节点的 LLM 增量块，前端打字机渲染正文
#    ("answer", JSON) ：structure_answer 节点整理好的 ChefAnswer 整包 JSON，前端画卡片
#  structure_answer 节点里结构化链自身的 token 碎片被丢弃（半截 JSON 没意义）。
# --------------------------------------------------------------------------- #
def _stream_agent(message, session_id):
    """公共流式生成器：按"消息来自哪个节点"分流输出。"""
    config = {"configurable": {"thread_id": session_id}}
    for message_chunk, metadata in agent.stream(
        {"messages": [message]},
        config=config,
        stream_mode="messages",
    ):
        node = metadata.get("langgraph_node")
        content = getattr(message_chunk, "content", "")
        if not content:
            continue
        # chef_think 的流式增量块(AIMessageChunk) → 正文逐字流给前端
        if node == "chef_think" and isinstance(message_chunk, AIMessageChunk):
            yield ("token", content)
        # structure_answer 节点返回的完整 JSON 消息 → 整包给前端；
        # 注意 AIMessageChunk 是 AIMessage 子类，必须显式排除链内部的流式碎片
        elif node == "structure_answer" and not isinstance(message_chunk, AIMessageChunk):
            yield ("answer", content)


def stream_agent(message, session_id):
    """流式核心：直接喂拼好的 message，供路由层 event_generator 调用。"""
    yield from _stream_agent(message, session_id)


def stream_agent_with_text(text, session_id):
    yield from _stream_agent(build_human_message(text), session_id)


def stream_agent_with_image_url(image_url, text, session_id):
    yield from _stream_agent(build_human_message(text, image_url), session_id)


def stream_agent_with_image(image_path, text, session_id):
    yield from stream_agent_with_image_url(image_to_oss_url(image_path), text, session_id)


if __name__ == "__main__":
    import asyncio
    answer = asyncio.run(ask_agent_with_image(
        r"D:\微信图片_20260729172003_642_58.jpg",
        "识别图片中的食材，并推荐健身能吃的菜。",
        "user_001",
    ))
    print(answer)
