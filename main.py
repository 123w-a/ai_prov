import mimetypes#显出JPG 图片 PNG 图片这俩种的区别
from langchain_core.messages import HumanMessage  #用户发送消息的类
from agent import agent  #调用我写的agent
from oss_utils import upload_to_oss  #把图片上传到OSS并返回公网URL


def image_to_oss_url(image_path):  #将本地图片上传OSS并返回可访问URL
    mime_type, _ = mimetypes.guess_type(image_path)  #获取图片的MIME类型
    if mime_type is None:  #如果没有MIME类型就按默认jpg图片格式处理
        mime_type = "image/jpeg"
    with open(image_path, "rb") as image_file:  #读取图片字节
        image_bytes = image_file.read()
    return upload_to_oss(image_bytes, mime_type)  #上传到OSS，返回公网URL


def ask_agent_with_image(image_path, text, session_id):#传图片加文字串进行对话
    image_url = image_to_oss_url(image_path)#把图片上传OSS得到URL



    message = HumanMessage(
        content=[
            {
                "type": "text",#表示纯文本
                "text": text
            },
            {
                "type": "image_url",#表示图片路径
                "image_url": {
                    "url": image_url
                }
            }
        ]
    )

    config = {  #会话记忆配置
        "configurable": {
            "thread_id": session_id
        }
    }

    response = agent.invoke(#就是将消息传给了agent
        {
            "messages": [message]
        },
        config=config#会话标识
    )

    return response["messages"][-1].content#返回ai结果


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


if __name__ == "__main__":
    answer = ask_agent_with_image(
        r"D:\微信图片_20260729172003_642_58.jpg",
        "识别图片中的食材，并推荐健身能吃的菜。",
        "user_001"
    )

    print(answer)
