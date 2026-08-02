import mimetypes#显出JPG 图片 PNG 图片这俩种的区别
from langchain_core.messages import HumanMessage, AIMessageChunk  #用户发送消息的类 + 流式增量块类型
from agent import agent  #调用我写的agent
from oss_utils import upload_to_oss  #把图片上传到OSS并返回公网URL


def image_to_oss_url(image_path):  #将本地图片上传OSS并返回可访问URL
    mime_type, _ = mimetypes.guess_type(image_path)  #获取图片的MIME类型
    if mime_type is None:  #如果没有MIME类型就按默认jpg图片格式处理
        mime_type = "image/jpeg"
    with open(image_path, "rb") as image_file:  #读取图片字节
        image_bytes = image_file.read()
    return upload_to_oss(image_bytes, mime_type)  #上传到OSS，返回公网URL


def image_bytes_to_oss_url(image_bytes, mime_type="image/jpeg"):
    """直接把图片 bytes 上传到 OSS 并返回可访问 URL（无需先落本地磁盘）。"""
    if mime_type is None:
        mime_type = "image/jpeg"
    return upload_to_oss(image_bytes, mime_type)


def ask_agent_with_image_url(image_url, text, session_id):
    """传 OSS 图片 URL + 文字串进行对话（URL 版，路由侧已上传好时用它）。"""
    message = HumanMessage(
        content=[
            {
                "type": "text",  # 表示纯文本
                "text": text
            },
            {
                "type": "image_url",  # 表示图片路径
                "image_url": {
                    "url": image_url
                }
            }
        ]
    )

    config = {  # 会话记忆配置
        "configurable": {
            "thread_id": session_id
        }
    }

    response = agent.invoke(  # 就是将消息传给了agent
        {
            "messages": [message]
        },
        config=config  # 会话标识
    )

    return response["messages"][-1].content  # 返回ai结果


def ask_agent_with_image(image_path, text, session_id):  #传图片加文字串进行对话
    image_url = image_to_oss_url(image_path)  #把图片上传OSS得到URL
    return ask_agent_with_image_url(image_url, text, session_id)


def ask_agent_with_text(text, session_id):#传文字串进行对话
    message = HumanMessage(
        content=text
    )

    config = {  #会话记忆配置
        "configurable": {
            "thread_id": session_id
        }
    }

    response = agent.invoke(
        {
            "messages": [message]
        },
        config=config
    )

    return response["messages"][-1].content


# --------------------------------------------------------------------------- #
#  流式版本：和上面的阻塞函数一一对应，但用 agent.stream() 逐 token 吐出
#  调用方式从 .invoke() 换成 .stream(stream_mode="messages")，只过滤 LLM 增量 token，
#  最适合后端 SSE / 前端打字机推送。图拓扑、工具、断点、压缩逻辑完全不动。
#
#  两段式输出（LCEL 重构后）：每次 yield 一个 (kind, content) 元组——
#    ("token", 文字)  ：chef_think 节点的 LLM 增量块，前端打字机渲染正文
#    ("answer", JSON) ：structure_answer 节点整理好的 ChefAnswer 整包 JSON，前端画卡片
#  structure_answer 节点里结构化链自身的 token 碎片被丢弃（半截 JSON 没意义）。
# --------------------------------------------------------------------------- #
def _stream_agent(message, session_id):
    """公共流式生成器：按"消息来自哪个节点"分流输出"""
    config = {  #会话记忆配置
        "configurable": {
            "thread_id": session_id
        }
    }

    # stream_mode="messages" 返回 (消息, 元数据)，元数据的 langgraph_node 标明来自哪个节点
    for message_chunk, metadata in agent.stream(
        {
            "messages": [message]
        },
        config=config,
        stream_mode="messages"
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


def stream_agent_with_image_url(image_url, text, session_id):
    """流式：传 OSS 图片 URL + 文字（URL 版），两段式输出"""
    message = HumanMessage(
        content=[
            {
                "type": "text",  # 表示纯文本
                "text": text
            },
            {
                "type": "image_url",  # 表示图片路径
                "image_url": {
                    "url": image_url
                }
            }
        ]
    )
    yield from _stream_agent(message, session_id)


def stream_agent_with_image(image_path, text, session_id):  #流式：传图片加文字，逐 token 吐出
    image_url = image_to_oss_url(image_path)  #把图片上传OSS得到URL
    yield from stream_agent_with_image_url(image_url, text, session_id)


def stream_agent_with_text(text, session_id):#流式：传文字串，逐 token 吐出
    message = HumanMessage(
        content=text
    )
    yield from _stream_agent(message, session_id)


if __name__ == "__main__":
    answer = ask_agent_with_image(
        r"D:\微信图片_20260729172003_642_58.jpg",
        "识别图片中的食材，并推荐健身能吃的菜。",
        "user_001"
    )

    print(answer)
