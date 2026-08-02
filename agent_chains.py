# agent_chains.py：LCEL 链的组装地（prompt | llm | parser）
# 职责单一：把提示词模板、模型、输出解析器用 | 管道串成可运行的链
# 不含图流转逻辑（在 agent_graph.py），不含数据形状定义（在 agent_schemas.py）
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException  # LangChain 解析失败统一异常
from pydantic import ValidationError  # Pydantic schema 越界/缺字段校验异常

from model_name import get_langchain_llm
from agent_schemas import ChefAnswer

# --------------------------------------------------------------------------- #
#  1. Parser：按 ChefAnswer 生成"输出格式说明书"，并负责最终解析 + schema 校验
# --------------------------------------------------------------------------- #
chef_parser = PydanticOutputParser(pydantic_object=ChefAnswer)

# --------------------------------------------------------------------------- #
#  2. Prompt 模板：告诉模型"把对话上下文整理成 ChefAnswer JSON"
#     {format_instructions} 用 partial 预填成 schema 说明书（里面是 JSON schema，
#     自带大括号，预填可避免模板转义问题）；{context} 由图节点调用时传入
# --------------------------------------------------------------------------- #
STRUCTURE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "你是 AI 私厨的结构化整理员。根据对话上下文（用户食材/口味需求 + 工具搜索结果），"
        "整理出最终回答。规则：\n"
        "1. 菜谱必须来自上下文中的搜索结果，禁止编造上下文之外的菜；\n"
        "2. 难度/营养星级只给 1-5 的整数；调料用量必须带生活化比喻；"
        "步骤通俗严谨，写清火候和时间；\n"
        "3. image_url 只能填上下文中工具返回的图片链接，没有可靠图片就填 null，"
        "并在 image_note 里明说没找到、用文字描述口感；\n"
        "4. 只输出符合以下格式的 JSON，不要输出任何额外文字、解释或代码围栏。\n"
        "{format_instructions}",
    ),
    ("human", "对话上下文：\n{context}"),
]).partial(format_instructions=chef_parser.get_format_instructions())

# --------------------------------------------------------------------------- #
#  3. 组装 LCEL 链：prompt | llm | parser
#     温度调低保 JSON 稳定（不要创意要准确）；max_tokens 加大防多道菜时 JSON 被截断
#     | 是 LCEL 管道运算符：前一环的输出自动成为后一环的输入
# --------------------------------------------------------------------------- #
structure_llm = get_langchain_llm("gpt", temperature=0.2, max_tokens=2048)

chef_answer_chain = STRUCTURE_PROMPT | structure_llm | chef_parser


# --------------------------------------------------------------------------- #
#  4. 格式自动重试（Retry on Parsing Error）——业界标准做法
#     行业做法：Pydantic 解析失败时，把具体报错回灌 LLM，让它自我修正后重新解析，
#     最多重试 MAX_STRUCTURE_RETRIES 次；全部失败才把错误上抛，交上层降级为 markdown。
#     比"直接降级成 markdown"更稳健——结构化卡片成功率显著提升。
# --------------------------------------------------------------------------- #
MAX_STRUCTURE_RETRIES = 2

# 修正提示：让模型看到原始上下文 + 上一次的具体报错，重新产出合规 JSON
_STRUCTURE_FIX_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "你是 AI 私厨的结构化整理员。下面这次输出未能通过格式校验，请严格按格式说明书"
        "重新输出，不要输出任何额外文字、解释或代码围栏。\n{format_instructions}",
    ),
    (
        "human",
        "原始对话上下文：\n{context}\n\n"
        "上一次解析报错信息：\n{error}\n\n"
        "请根据上面报错修正，并重新输出完全符合格式的 JSON。",
    ),
]).partial(format_instructions=chef_parser.get_format_instructions())


def build_structured_answer(context: str) -> ChefAnswer:
    """对上下文做结构化，自带「格式自动重试」。

    执行流程：
      1. 首次：标准链 STRUCTURE_PROMPT | llm | parser；
      2. 若抛解析异常（ValidationError / OutputParserException），把报错 + 原文上下文
         喂给 _STRUCTURE_FIX_PROMPT 修正链，最多重试 MAX_STRUCTURE_RETRIES 次；
      3. 重试耗尽仍失败 → 上抛最后错误，由 agent_graph 的 except 降级为 markdown。
    """
    last_err: Exception | None = None
    for attempt in range(1 + MAX_STRUCTURE_RETRIES):
        try:
            if attempt == 0:
                return chef_answer_chain.invoke({"context": context})
            # 重试：专用修正链，把上次错误反馈给模型自我修正
            return (_STRUCTURE_FIX_PROMPT | structure_llm | chef_parser).invoke(
                {"context": context, "error": str(last_err)}
            )
        except (ValidationError, OutputParserException) as e:
            last_err = e
    # 重试耗尽，抛出最后错误，交给 structure_answer_node 的降级逻辑
    raise last_err  # type: ignore[arg-type]


def rank_recipes(answer: ChefAnswer) -> ChefAnswer:
    """链后处理：排序规则不靠模型"自觉"，由 Python 精确执行——
    第一优先级营养从高到低，第二优先级难度从易到难（与系统提示词第 3 条一致）"""
    answer.recipes.sort(key=lambda r: (-r.nutrition, r.difficulty))
    return answer
