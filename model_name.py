from configs import MODEL_CONFIGS#传入模型参数
from langchain_openai import ChatOpenAI#创键langchain的对象

def get_langchain_llm(provider):#创键lang chain对象,不需要额外处理模型
    configs = MODEL_CONFIGS[provider]
    llm=ChatOpenAI(
        model=configs['model_name'],
        api_key=configs['api_key'],
        base_url=configs['base_url'],
        max_tokens=1024,
        temperature=0.7
    )
    return llm
