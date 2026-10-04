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


class Ingredient(BaseModel):
    """主食材（带克数）。

    与 seasonings 分开的理由：调料用「一小勺」「一撮」这类生活化比喻就够，而营养数值
    只认克数——比喻算不出数值。两者混在一起时，要么调料被迫编克数，要么主食材拿不到用量，
    结果就是那张营养表永远算不出来。

    amount_g 允许为 null，且**必须是正文真给了克数才填**：这一步的职责是提取，不是补全
    （见 agent_chains.py 的 STRUCTURE_PROMPT 第 1 条）。算不出数值比写一个像模像样的错数字好。
    """
    name: str = Field(description="食材名，如 西兰花")
    amount_g: Optional[float] = Field(
        default=None,
        description="可食部克数，只填数字不要单位；正文没给克数就填 null，禁止按常识补一个",
    )


class Recipe(BaseModel):#菜谱结构
    """菜谱"""
    name: str = Field(description="菜名")
    intro: str = Field(description="30字内简介")
    difficulty: int = Field(ge=1, le=5, description="难度1-5")
    nutrition: int = Field(ge=1, le=5, description="营养1-5")
    ingredients: list[Ingredient] = Field(
        default_factory=list,
        description="主食材与克数（营养数值表的输入；正文没给用量就留空）",
    )
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


class NutrientValue(BaseModel):
    """一项营养：值 + 它到底有没有值。

    0 和「没有值」必须分开表达：0 克膳食纤维是一句结论，「未收录」只是一句实话。
    合成一个数字字段（缺失就写 0）会让页面把「没查到」印成「没有」。
    """
    value: Optional[float] = Field(default=None, description="数值；未收录时为 null")
    status: str = Field(default="unavailable", description="known | unavailable")


class NutritionIngredient(BaseModel):
    """营养账目里的一行：这样食材有没有被算进去，以及算的是国标表里哪一条。"""
    name: str = Field(description="正文里的食材名")
    amount_g: Optional[float] = Field(default=None, description="克数；没有则为 null")
    lookup_name: Optional[str] = Field(
        default=None, description="映射到的国标表条目名；未收录为 null（页面可追溯）",
    )
    status: str = Field(description="covered（算进去了）| missing（表里没收录）| no_amount（没有克数）")


class NutritionFacts(BaseModel):
    """这顿的营养数值表。

    ⚠️ 这个模型**不由模型填写**：agent_graph 在结构化成 ChefAnswer 之后，用
    domain/dish_nutrition.py 从 recipes[0].ingredients 确定性算出后赋值。放进 schema
    只是为了让前端拿到稳定形状，并让存量数据反序列化时不缺字段。

    status 三档的含义是全篇最关键的一处诚实标注：
      complete    全部食材已收录且都有克数
      partial     算出了小计，但有食材未收录或缺克数 —— 页面必须说明这是「已覆盖食材的小计」
      unavailable 一个都没算成（无食材 / 全未收录 / 全无克数）
    """
    status: str = Field(default="unavailable", description="complete | partial | unavailable")
    nutrients: dict[str, NutrientValue] = Field(
        default_factory=dict,
        description="六项，键固定为 energy_kcal/protein_g/fat_g/carb_g/fiber_g/sodium_mg",
    )
    ingredients: list[NutritionIngredient] = Field(
        default_factory=list, description="算这张表用到的食材账目"
    )
    basis: str = Field(
        default="recipe_ingredient_amounts",
        description="计算口径：按菜谱食材用量 × 国标《中国食物成分表》",
    )
    covered: list[str] = Field(default_factory=list, description="算进去了的食材名")
    missing: list[str] = Field(default_factory=list, description="国标表未收录的食材名")
    no_amount: list[str] = Field(default_factory=list, description="有食材但正文没给克数的")


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
        description="pass/warn/adjusted/blocked/member_conflict"
    )
    reason: str = Field(default="", description="原因")


class DishMatrixItem(BaseModel):
    """同餐菜品与家庭成员的确定性可用性矩阵。"""
    dish: str = Field(description="菜名")
    member: str = Field(description="成员名")
    verdict: str = Field(description="可吃 / 需调整 / 待确认 / 不可吃")
    reason: str = Field(default="", description="判断理由")


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
        description="2-4句具体讲解：推荐理由、对当前约束的适配方式、关键烹饪提醒，禁止空泛套话"
    )
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="依据",
    )
    health_lights: list[HealthLight] = Field(
        default_factory=list,
        description="红绿灯",
    )
    nutrition_facts: Optional[NutritionFacts] = Field(
        default=None,
        description="这顿的营养数值表（系统按食材用量算出，模型不填）",
    )
    guardrails: list[GuardrailItem] = Field(
        default_factory=list,
        description="护栏结论",
    )
    # 联合决策最小闭环：字段保持可选空默认，旧调用方和存量卡片无需迁移。
    member_adjustments: list[str] = Field(
        default_factory=list,
        description="给每位同餐成员的一句话调整建议",
    )
    dish_matrix: list[DishMatrixItem] = Field(
        default_factory=list,
        description="菜品与成员的可用性矩阵",
    )
