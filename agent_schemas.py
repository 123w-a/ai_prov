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


class ChefAnswer(BaseModel):#最顶层的大模型其中嵌套了各种菜谱
    """AI 私厨最终回答的完整结构（对应前端一张完整的回答卡片）"""
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
    chef_tip: str = Field(#厨师小建议
        default="",
        description="作为厨师的小建议，简短 3 句以内"
    )
