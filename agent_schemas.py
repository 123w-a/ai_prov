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
    """调料"""
    name: str = Field(description="名称")
    amount: str = Field(description="用量，带生活化比喻")


class Recipe(BaseModel):#菜谱结构
    """菜谱"""
    name: str = Field(description="菜名")
    intro: str = Field(description="30字内简介")
    difficulty: int = Field(ge=1, le=5, description="难度1-5")
    nutrition: int = Field(ge=1, le=5, description="营养1-5")
    seasonings: list[Seasoning] = Field(#列表元素类型 Seasoning，列表中嵌套列表
        default_factory=list,
        description="调料清单"#不同材料的描述
    )
    steps: list[str] = Field(#列表元素类型 str，列表中嵌套列表
        min_length=1,#最少1个步骤
        description="步骤，含火候时间"
    )
    image_url: Optional[str] = Field(
        default=None,
        description="图片链接"
    )
    image_ai_generated: bool = Field(
        default=False,
        description="是否AI图"
    )


class HealthLight(BaseModel):
    """红绿灯"""
    label: str = Field(description="钠/糖/脂肪")
    level: str = Field(description="green | yellow | red")
    reason: str = Field(default="", description="理由")


class SourceRef(BaseModel):#权威信息
    """出处"""
    source: str = Field(description="文件名")
    section: str = Field(default="", description="章节")
    snippet: str = Field(default="", description="片段")
    category: str = Field(default="", description="类别")


class GuardrailItem(BaseModel):
    """护栏"""

    condition: str = Field(description="健康标签")
    rule: str = Field(
        default="",
        description="膳食约束",
    )
    status: str = Field(
        description="pass/warn/adjusted"
    )
    reason: str = Field(default="", description="原因")


class ChefAnswer(BaseModel):#最顶层的大模型其中嵌套了各种菜谱
    """回答"""
    recipes: list[Recipe] = Field(#列表元素类型 Recipe，列表中嵌套列表
        min_length=1,#最少1道菜
        description="推荐菜"
    )
    image_url: Optional[str] = Field(#图片链接
        default=None,
        description="图片链接"
    )
    image_ai_generated: bool = Field(#图片是否为 AI 生成的
        default=False,
        description="是否AI图"
    )
    image_requested: bool = Field(
        default=False,
        description="是否要图",
    )
    image_note: str = Field(#图片注解
        default="",
        description="图注"
    )
    chef_tip: str = Field(#膳食管家小建议
        default="",
        description="3句内建议"
    )
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="依据",
    )
    health_lights: list[HealthLight] = Field(
        default_factory=list,
        description="红绿灯",
    )
    guardrails: list[GuardrailItem] = Field(
        default_factory=list,
        description="护栏结论",
    )
