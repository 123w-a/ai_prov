# agent_schemas.py：结构化输出的"数据形状"定义（pydantic 模型）
# 职责单一：只规定最终回答长什么样，不含任何 LLM 调用、不含任何图逻辑
# 配套关系：agent_chains.py 里的 PydanticOutputParser(pydantic_object=ChefAnswer) 按这里
#           的 schema 生成 format_instructions（告诉模型输出格式），并校验/解析模型输出
#整条链路：Field 描述 → Parser 提取格式指令 → 填充进 Prompt 模板 → LLM 按规则输出 JSON → Pydantic 校验解析。
from typing import Optional
from pydantic import BaseModel, Field

#field主要的作用就是给特殊字段加上规则和限制
#description是给字段添加描述，归field管
class Seasoning(BaseModel):#调料结构#告诉模型输入的东西必须是什么格式
    """单个调料：名称 + 用量（克数必须带生活化比喻，来自系统提示词第 5 条要求）"""
    name: str = Field(description="调料名称，如：盐、生抽、蒜")
    amount: str = Field(description="用量，必须带生活化比喻，如：'3g，约半小勺'、'5ml，约一矿泉水瓶盖'")


class Recipe(BaseModel):#菜谱结构
    """一道菜的结构化描述。星级是 1-5 整数，前端负责画星星，模型只给数字"""
    name: str = Field(description="菜名")
    intro: str = Field(description="菜品一句话简介，30字以内，说清口味特点")
    difficulty: int = Field(ge=1, le=5, description="难度星级，1=新手零失败，5=需要功底")
    nutrition: int = Field(ge=1, le=5, description="营养星级，1=重油重盐解馋向，5=高蛋白低脂均衡")
    seasonings: list[Seasoning] = Field(#列表元素类型 Seasoning，列表中嵌套列表
        default_factory=list,
        description="做菜要给的调料参数清单，不含主料"#不同材料的描述
    )
    steps: list[str] = Field(#列表元素类型 str，列表中嵌套列表
        min_length=1,#最少1个步骤
        description="做菜步骤，每步一句话，通俗适合家庭厨房，火候和时间要写清"
    )
    image_url: Optional[str] = Field(
        default=None,
        description="这道菜对应的成品图链接。多道菜时每道菜必须使用自己的对应图片，没有可靠图片就填 null"
    )
    image_ai_generated: bool = Field(
        default=False,
        description="这道菜的图片是否为 AI 生成示意图；由系统根据图片来源设置，模型不要自行编造"
    )


class SourceRef(BaseModel):#权威信息
    """一条权威引文出处，来自 nutrition_kb_search 的检索结果，体现可溯源与 AIGC 透明标注。"""
    source: str = Field(description="出处文件名，如《成人高血压食养指南（2023年版）》")
    section: str = Field(default="", description="出处章节/条目名，原样取自 nutrition_kb_search 返回的 section 字段，如『三、生活方式指导』；工具未返回则留空，禁止编造")
    snippet: str = Field(default="", description="该出处命中片段的简短摘录，证明结论有据可依")
    category: str = Field(default="", description="知识库分层类别，如 1_慢病食养指南 / 2_营养素参考摄入量DRI")


class GuardrailItem(BaseModel):
    """健康护栏的一项审计结论（前端右侧『本轮健康护栏』面板的数据来源）。

    由 ``agent_graph.structure_answer_node`` 依据 ``verify_answer_node`` 的确定性
    审计结果**运行时注入**，而不是让 LLM 自由生成——保证硬约束结论 100% 可信、
    可溯源，是项目『健康链透明』亮点的数据底座。
    """

    condition: str = Field(description="适用人群/病种标签，如：高血压、糖尿病、孕期")
    rule: str = Field(
        default="",
        description="对该人群的核心膳食约束，如：限盐（每日<5g）、忌高嘌呤",
    )
    status: str = Field(
        description=(
            "审计结论：pass=已符合 / warn=需注意（已达重生成上限仍存风险）/ "
            "adjusted=初始方案命中硬禁忌、已被健康护栏自动调整至合规"
        )
    )
    reason: str = Field(default="", description="一句中文说明，解释为什么是这个结论")


class ChefAnswer(BaseModel):#最顶层的大模型其中嵌套了各种菜谱
    """小膳管家最终回答的完整结构（对应前端一张完整的回答卡片）"""
    recipes: list[Recipe] = Field(#列表元素类型 Recipe，列表中嵌套列表
        min_length=1,#最少1道菜
        description="最终只返回最合适的一道菜；排序不用模型管，Python 会按营养降序、难度升序重排"
    )
    image_url: Optional[str] = Field(#图片链接
        default=None,
        description="成品图 OSS 公网链接。只能填 web_search 工具返回的 image_url；"
                    "工具没给到可靠图片时必须填 null，禁止编造任何图片地址"
    )
    image_ai_generated: bool = Field(#图片是否为 AI 生成的
        default=False,
        description="图片是否为 AI 生成的示意图（非真实成品照）。"
                    "true 时前端【必须】明示『AI 生成示意图』，绝不能伪装成真实成品照——"
                    "这是项目的透明标注亮点，由系统根据 web_search 返回的 image_source 自动判定，"
                    "模型无需、也不应自行改为 true"
    )
    image_note: str = Field(#图片注解
        default="",
        description="有图：一句话图注（若 image_ai_generated 为 true，应说明这是 AI 生成示意图、非真实成品照）；"
                    "无图：明确说没找到合适图片，并用文字描述这道菜应有的口感"
    )
    chef_tip: str = Field(#膳食管家小建议
        default="",
        description="作为膳食管家的小建议，简短 3 句以内"
    )
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="本次回答引用的权威依据出处列表，来自 nutrition_kb_search 检索结果；"
                    "凡涉及健康/忌口/标签的结论都必须在此列出对应出处文件名与片段，体现可溯源与 AIGC 透明标注；"
                    "无健康相关结论时可为空列表",
    )
    guardrails: list[GuardrailItem] = Field(
        default_factory=list,
        description="本次回答针对各健康标签的护栏审计结论列表，由图后端确定性注入（非 LLM 生成）；"
                    "前端右侧面板据此渲染『本轮健康护栏』，把 RAG/护栏能力变成用户可感知的产品能力。"
                    "无健康相关标签时为空列表。",
    )
