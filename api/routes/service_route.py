"""上门私厨服务的演示与远期愿景路由。

当前阶段：
- 已实现：文字食材清单的确定性别名匹配与缺菜计算。
- 未实现：图片识别、语音识别、真实预约、支付、派单和履约。

本路由不在前端冒充“已经能下单”，而是把能力边界作为接口数据返回，
方便产品展示时明确“方向完整但能力未接入”。
"""

from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter()


HOME_SERVICE_VISION = {
    "name": "上门私厨 O2O",
    "status": "planned",
    "current_stage": "vision",
    "summary": "在现有 AI 膳食决策能力基础上，远期连接家庭与持证厨师，形成上门私厨服务。",
    "current_capabilities": [
        "AI 菜谱生成与健康护栏",
        "本地营养知识库检索与可溯源引用",
        "会话记忆与用户长期饮食偏好",
    ],
    "roadmap": [
        {
            "phase": 1,
            "title": "AI 膳食决策中枢",
            "status": "done",
            "description": "完成菜谱推荐、健康禁忌审计、营养知识库检索与透明标注。",
        },
        {
            "phase": 2,
            "title": "附近餐饮导航",
            "status": "partial",
            "description": "接入或预置附近餐厅推荐，形成“在家做 / 出门吃”的轻量决策入口。",
        },
        {
            "phase": 3,
            "title": "上门私厨 O2O",
            "status": "planned",
            "description": "引入持证厨师认证、服务预约、订单、支付、位置、履约与隐私保护能力。",
        },
    ],
    "future_dependencies": [
        "厨师实名认证与资质核验",
        "服务预约与派单系统",
        "在线支付与售后",
        "用户位置与家庭隐私保护",
        "食品安全与责任保险",
    ],
    "privacy_note": "上门场景涉及家庭住址、联系方式、健康饮食偏好等敏感信息，正式实现前必须完成最小化采集、授权、加密存储与合规说明。",
}


class ServicePreviewRequest(BaseModel):
    recipe_name: str = Field(..., min_length=1, description="目标菜名")
    inventory_text: str = Field("", description="用户文字输入的已有食材")
    image_url: Optional[str] = Field(None, description="用户上传图片的 OSS 地址")
    mode: str = Field("home_chef", description="home_chef / voice / text")
    expected_ingredients: Optional[list[str]] = Field(
        None,
        description="可选：由调用方提供的所需食材，未传则使用内置演示菜谱库",
    )


# 演示阶段只收录少量高频菜。若菜名不在库中，接口会明确返回 recipe_matched=false，
# 不猜答案，也不伪装成已经识别。
HOME_CHEF_RECIPES: dict[str, list[str]] = {
    "青椒牛肉": ["牛肉", "青椒", "洋葱", "生抽", "食用油", "盐", "料酒", "淀粉"],
    "番茄炒蛋": ["番茄", "鸡蛋", "葱", "盐", "糖", "食用油"],
    "番茄鸡蛋面": ["番茄", "鸡蛋", "挂面", "葱", "盐", "食用油"],
    "蒜蓉西兰花": ["西兰花", "大蒜", "盐", "食用油"],
    "清蒸鲈鱼": ["鲈鱼", "姜", "葱", "蒸鱼豉油", "食用油"],
    "酸辣土豆丝": ["土豆", "青椒", "干辣椒", "醋", "盐", "食用油"],
    # T2-P1 扩容：家常高频 16 道
    "红烧肉": ["五花肉", "冰糖", "生抽", "老抽", "料酒", "姜", "八角", "食用油"],
    "红烧排骨": ["排骨", "冰糖", "生抽", "老抽", "料酒", "姜", "八角"],
    "鱼香肉丝": ["猪里脊", "木耳", "胡萝卜", "青椒", "郫县豆瓣酱", "醋", "糖", "生抽", "淀粉"],
    "麻婆豆腐": ["豆腐", "牛肉", "郫县豆瓣酱", "花椒", "大蒜", "生抽", "食用油"],
    "宫保鸡丁": ["鸡胸肉", "花生米", "干辣椒", "葱", "糖", "醋", "生抽", "淀粉"],
    "可乐鸡翅": ["鸡翅", "可乐", "生抽", "姜"],
    "尖椒肉丝": ["猪里脊", "尖椒", "生抽", "盐", "淀粉", "食用油"],
    "韭菜炒蛋": ["韭菜", "鸡蛋", "盐", "食用油"],
    "西红柿鸡蛋汤": ["番茄", "鸡蛋", "葱", "盐", "香油"],
    "凉拌黄瓜": ["黄瓜", "大蒜", "醋", "香油", "糖"],
    "手撕包菜": ["包菜", "干辣椒", "大蒜", "醋", "生抽", "食用油"],
    "蒜蓉粉丝虾": ["大虾", "粉丝", "大蒜", "蒸鱼豉油", "食用油"],
    "清蒸鳕鱼": ["鳕鱼", "姜", "葱", "蒸鱼豉油", "食用油"],
    "香菇滑鸡": ["香菇", "鸡腿肉", "生抽", "蚝油", "淀粉", "姜"],
    "咖喱鸡肉饭": ["鸡腿肉", "土豆", "胡萝卜", "洋葱", "咖喱块", "米饭"],
    "冬瓜排骨汤": ["冬瓜", "排骨", "姜", "盐", "料酒"],
    "清炒时蔬": ["应季青菜", "大蒜", "盐", "食用油"],
}


INGREDIENT_ALIASES: dict[str, list[str]] = {
    "番茄": ["西红柿"],
    "鸡蛋": ["蛋"],
    "青椒": ["柿子椒", "甜椒"],
    "牛肉": ["牛里脊", "肥牛"],
    "生抽": ["酱油"],
    "挂面": ["面条"],
    "食用油": ["油"],
    "大蒜": ["蒜"],
    "土豆": ["马铃薯"],
    "鲈鱼": ["鱼"],
    "蒸鱼豉油": ["豉油"],
    "猪里脊": ["瘦肉", "里脊", "猪肉"],
    "鸡胸肉": ["鸡脯肉", "鸡肉"],
    "鸡腿肉": ["鸡腿"],
    "排骨": ["肋排"],
    "包菜": ["卷心菜", "圆白菜", "莲花白"],
    "大虾": ["虾", "基围虾", "明虾"],
    "尖椒": ["辣椒", "杭椒"],
    "米饭": ["大米"],
    "咖喱块": ["咖喱"],
    "花生米": ["花生"],
    "应季青菜": ["青菜", "白菜", "油菜", "菠菜"],
}


def _split_inventory_text(text: str) -> list[str]:
    """把“鸡蛋2个、番茄、家里还有青椒”清洗成可用于匹配的食材 token。"""
    if not text or not text.strip():
        return []

    # 去掉“鸡蛋2个 / 牛肉200g”里的数量与单位，只保留食材名称。
    cleaned = re.sub(
        r"\d+(?:\.\d+)?\s*(?:克|g|毫升|ml|千克|kg|个|只|根|勺|汤匙|茶匙|斤|两)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(有|还有|家里有|家里还有|冰箱里有|我有|剩下|目前有)+",
        "",
        cleaned,
    )

    raw_parts = re.split(r"[,，、;；。\n\r\t|/]+", cleaned)
    tokens: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        part = re.sub(r"\s+", "", part).strip("。，、；; ")
        if not part:
            continue
        part = re.sub(r"^(有|还有|家里有|家里还有|冰箱里有|我有|剩下|目前有)+", "", part)
        part = re.sub(r"\d+(?:\.\d+)?$", "", part)
        part = part.strip()
        if part and part not in seen:
            tokens.append(part)
            seen.add(part)
    return tokens


def _has_ingredient(available: list[str], required: str) -> bool:
    """按别名做包含匹配，不调用任何模型，保证结果稳定可解释。"""
    candidates = [required] + INGREDIENT_ALIASES.get(required, [])
    for item in available:
        for candidate in candidates:
            if candidate in item or item in candidate:
                return True
    return False


def _build_preview(data: ServicePreviewRequest) -> dict:
    """计算文字食材缺口，同时诚实标注图片/语音/下单尚未接入。"""
    if data.mode == "voice":
        return {
            "status": "demo",
            "mode": data.mode,
            "recipe_name": data.recipe_name,
            "recipe_matched": data.recipe_name in HOME_CHEF_RECIPES,
            "detected_from_text": [],
            "required_ingredients": HOME_CHEF_RECIPES.get(data.recipe_name, []),
            "missing_ingredients": HOME_CHEF_RECIPES.get(data.recipe_name, []),
            "chef_can_bring": HOME_CHEF_RECIPES.get(data.recipe_name, []),
            "image_received": bool(data.image_url),
            "image_recognition_supported": False,
            "voice_input_received": True,
            "voice_input_supported": True,
            "voice_recognition_available": bool(os.getenv("DASHSCOPE_API_KEY")),
            "voice_recognition_route": "/api/transcribe",
            "voice_to_service_integrated": False,
            "voice_status": "语音转文字接口 /api/transcribe 已接入，但尚未自动带入上门私厨缺口计算。",
            "order_supported": False,
            "blocked_reason": "图片识别、语音到上门私厨业务流的自动串联、真实预约/支付/派单尚未接入。",
        }

    required = list(data.expected_ingredients or HOME_CHEF_RECIPES.get(data.recipe_name, []))
    available = _split_inventory_text(data.inventory_text)
    missing = [item for item in required if not _has_ingredient(available, item)]

    return {
        "status": "demo",
        "mode": data.mode,
        "recipe_name": data.recipe_name,
        "recipe_matched": data.recipe_name in HOME_CHEF_RECIPES,
        "required_ingredients": required,
        "detected_from_text": available,
        "missing_ingredients": missing,
        "chef_can_bring": missing,
        "image_received": bool(data.image_url),
        "image_recognition_supported": False,
        "image_recognition_message": (
            "图片已接收。当前版本暂不支持食材识别，请先用文字补充冰箱里有哪些食材。"
            if data.image_url
            else "未上传图片。"
        ),
        "voice_input_received": False,
        "voice_input_supported": False,
        "order_supported": False,
        "blocked_reason": "图片识别、语音录入、真实预约/支付/派单尚未接入；当前仅支持文字清单缺口计算。",
    }


@router.get("/service/vision")
def service_vision() -> dict:
    """返回上门私厨服务的远期规划，仅用于产品展望和能力边界说明。"""

    return {
        "code": 200,
        "messages": "上门私厨服务为远期规划，当前仅提供能力预览",
        "data": HOME_SERVICE_VISION,
    }


@router.post("/service/preview")
def service_preview(payload: ServicePreviewRequest) -> dict:
    """计算已有食材与目标菜谱之间的缺口，并返回当前能力边界。

    这是真实可跑的预览接口：文字食材会做确定性别名匹配；
    图片、语音和下单不会返回伪造结果，而是明确标记为 false。
    """
    if payload.recipe_name not in HOME_CHEF_RECIPES and payload.expected_ingredients is None:
        raise HTTPException(
            status_code=400,
            detail="演示菜谱库暂未收录该菜名，请传入 expected_ingredients 或先选择已收录菜谱",
        )

    preview_data = _build_preview(payload)
    return {
        "code": 200,
        "messages": "当前为功能演示，尚未开放真实预约",
        "data": preview_data,
    }