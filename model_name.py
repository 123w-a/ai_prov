from configs import MODEL_CONFIGS#传入模型参数
from langchain_openai import ChatOpenAI#创键langchain的对象

#不用管模型的厂商，只需要传入模型参数

def get_langchain_llm(provider, temperature=0.7, max_tokens=1024):#创键lang chain对象,不需要额外处理模型
    # temperature/max_tokens 做成可选参数：默认值与之前完全一致（0.7/1024），老调用零影响；
    # 结构化输出场景（LCEL parser 链）传低温度+大 token，保证 JSON 稳定、不被截断
    configs = MODEL_CONFIGS[provider]
    llm=ChatOpenAI(
        model=configs['model_name'],
        api_key=configs['api_key'],
        base_url=configs['base_url'],
        max_tokens=max_tokens,
        temperature=temperature,
        streaming=True  # 开启流式：让 LangGraph 的 .stream() 能逐 token 吐出最终回答（.invoke() 仍正常返回完整消息）
    )
    return llm
