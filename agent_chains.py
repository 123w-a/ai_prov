# agent_chains.py：LCEL 链的组装地（prompt | llm | parser）
# 职责单一：把提示词模板、模型、输出解析器用 | 管道串成可运行的链
# 不含图流转逻辑（在 agent_graph.py），不含数据形状定义（在 agent_schemas.py）
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.exceptions import OutputParserException  # LangChain 解析失败统一异常
from pydantic import ValidationError  # Pydantic schema 越界/缺字段校验异常

from model_name import get_langchain_llm
from agent_schemas import ChefAnswer#就是要回答的东西得到全部规范

# --------------------------------------------------------------------------- #
#  1. Parser：按 ChefAnswer 生成"输出格式说明书"，并负责最终解析 + schema 校验
# --------------------------------------------------------------------------- #
chef_parser = PydanticOutputParser(pydantic_object=ChefAnswer)#做出实例按照chefanswer的规则
#向下解析 LLM 返回的 JSON 字符串，实例化为 Python 对象（校验 + 反序列化）做了2个事情
# --------------------------------------------------------------------------- #
#  2. Prompt 模板：告诉模型"把对话上下文整理成 ChefAnswer JSON"
#     {format_instructions} 用 partial 预填成 schema 说明书（里面是 JSON schema，
#     自带大括号，预填可避免模板转义问题）；{context} 由图节点调用时传入
# --------------------------------------------------------------------------- #
STRUCTURE_PROMPT = ChatPromptTemplate.from_messages([#专门的对话式提示词
    (#按照我定义的规则定义的最终提示词
        "system",
        "你是 小膳管家的结构化整理员。根据对话上下文（用户食材/口味需求 + 工具搜索结果），"
        "整理出最终回答。规则：\n"
        "1. 菜谱必须来自上下文中的搜索结果，禁止编造上下文之外的菜；\n"
        "2. 难度/营养星级只给 1-5 的整数；调料用量必须带生活化比喻；"
        "步骤通俗严谨，写清火候和时间；\n"
        "3. image_url 由系统在确定唯一菜名后匹配对应成品图，没有可靠图片就填 null，"
        "并在 image_note 里明说没找到、用文字描述口感；\n"
        "4. 默认 recipes 只包含最合适的一道菜；只有当用户明确要求多个选择、几道菜或供选择时，才允许输出多道菜；\n"
        "5. image_url 必须对应 recipes 中的菜名；没有可靠图片就填 null；\n"
        "6. 只输出符合以下格式的 JSON，不要输出任何额外文字、解释或代码围栏。\n"
        "7. 若上下文中出现『权威健康依据检索结果（来自 nutrition_kb_search）』块，必须将其中的 source 文件名与命中片段填入 sources 字段，"
        "每条含 source（文件名）、section（章节/条目名，原样照抄工具返回的 section 字段，没有则留空串）与 snippet（片段摘录）；凡健康/忌口/标签类结论都要有对应出处，无则 sources 留空列表。\n"
        "9. health_lights：依据 recipes 实际用料给钠/糖/脂肪三盏灯（level 取 green/yellow/red，附一句话 reason）；拿不准的维度可省略。\n"
        "{format_instructions}",# 预填充永久不变的模板变量
    ),
    ("human", "对话上下文：\n{context}"),#永远变化的我问这个大模型的问题
]).partial(format_instructions=chef_parser.get_format_instructions())#固定模板预填格式说明书

# --------------------------------------------------------------------------- #
#  3. 组装 LCEL 链：prompt | llm | parser
#     温度调低保 JSON 稳定（不要创意要准确）；max_tokens 加大防多道菜时 JSON 被截断
#     | 是 LCEL 管道运算符：前一环的输出自动成为后一环的输入
# --------------------------------------------------------------------------- #
structure_llm = get_langchain_llm("deepseek", temperature=0.2, max_tokens=2048)

chef_answer_chain = STRUCTURE_PROMPT | structure_llm | chef_parser#这里接受通过链后的 PydanticOutputParser类示例


# --------------------------------------------------------------------------- #
# LangChain 结构化输出自动重试修复机制 的核心提示词配置
# 专门解决大模型输出 JSON 格式错误、Pydantic 解析失败问题
# --------------------------------------------------------------------------- #
MAX_STRUCTURE_RETRIES = 2#最大尝试次数

# 修正提示：让模型看到原始上下文 + 上一次的具体报错，重新产出合规 JSON
_STRUCTURE_FIX_PROMPT = ChatPromptTemplate.from_messages([#写一个修正的提示词，对话提示词
    (
        "system",
        "你是 小膳管家的结构化整理员。下面这次输出未能通过格式校验，请严格按格式说明书"
        "重新输出：默认只保留最合适的一道菜；如果原始上下文明确要求多道菜，才保留对应的多道菜；"
        "不要输出任何额外文字、解释或代码围栏。\n{format_instructions}",
    ),
    (
        "human",
        "原始对话上下文：\n{context}\n\n"
        "上一次解析报错信息：\n{error}\n\n"
        "请根据上面报错修正，并重新输出完全符合格式的 JSON。",
    ),
]).partial(format_instructions=chef_parser.get_format_instructions())#在提取这个实例对象里面的结构化输出规则

# ValidationError
# 来自 Pydantic，JSON 结构合法，但字段不符合 ChefAnswer 模型约束：
# 星级填了 0/6、类型传了字符串数字
# 必填字段缺失、数组为空、图片链接类型错误等 schema 校验失败
# OutputParserException
# 来自 LangChain 解析器，LLM 输出本身就不是可解析 JSON：
# 包裹了 ```json 代码块、前后带解释文字
# JSON 少逗号、少括号、语法直接崩坏
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


def rank_recipes(answer: ChefAnswer, allow_multiple: bool = False) -> ChefAnswer:
    """链后处理：排序规则不靠模型"自觉"，由 Python 精确执行——
    第一优先级营养从高到低，第二优先级难度从易到难（与系统提示词第 3 条一致）"""
    answer.recipes.sort(key=lambda r: (-r.nutrition, r.difficulty))#拿菜谱排序
    if not allow_multiple:
        answer.recipes = answer.recipes[:1]
    return answer
