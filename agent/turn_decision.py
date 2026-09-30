"""统一的本轮产品形态决策。"""

from dataclasses import asdict, dataclass
from typing import Any


TURN_INTENTS = {
    "recommend",
    "confirm_one",
    "change_one",
    "followup",
    "restaurant",
    "home_service",
    "other",
}


def looks_like_dining_request(text: str) -> bool:
    """识别做菜、点餐、饮食建议等饮食相关请求。"""
    text = str(text or "").strip()
    if not text:
        return False
    markers = (
        "做饭", "做菜", "菜谱", "食谱", "菜品", "食材", "配方", "烹饪", "做法",
        "帮我做", "做道", "做个", "做一份", "做一下", "来道", "来个",
        "推荐", "吃", "饭", "餐", "早餐", "午餐", "晚餐", "夜宵", "外卖", "点餐", "食堂",
        "汤面", "面条", "米粉", "米线", "炒肉", "家常菜",
        "餐厅", "冰箱", "营养", "热量", "减脂", "控糖", "高血压", "糖尿病",
        "痛风", "尿酸", "健康饮食", "附近吃什么",
    )
    return any(marker in text for marker in markers)


def is_restaurant_ordering_scene(
    text: str,
    *,
    has_prior_restaurant: bool = False,
) -> bool:
    """餐厅/外食点餐请求只保留纯文本，不进入菜谱卡片结构化。

    ``has_prior_restaurant`` 只用于「换一家」这类没有地点词的延续指令。
    默认 False，确保路由层单独看一句话时不会把无上下文的换店词误判成外食。
    """
    text = str(text or "").strip()
    if not text:
        return False
    restaurant_markers = (
        "餐厅", "饭店", "饭馆", "餐馆", "菜馆", "酒馆", "酒吧", "大排档",
        "夜宵店", "宵夜店", "店里", "到店", "堂食", "外食", "外吃", "外出就餐",
        "出去吃", "出去吃饭", "在外吃", "在外面吃", "下馆子", "聚餐",
        "点餐", "点单", "菜单", "套餐", "档口", "食堂", "外卖", "附近",
    )
    restaurant_context = ("店", "餐厅", "饭店", "食堂", "外卖", "附近")
    signature_words = ("招牌", "推荐几道菜", "推荐几个菜", "点什么菜")
    cooking_markers = (
        "做法", "怎么做", "菜谱", "食谱", "烹饪", "开火", "下锅",
        "食材", "冰箱", "在家做", "自己做",
    )
    occasion_words = (
        "夜宵", "宵夜", "配酒", "喝酒", "酒局", "下酒", "聚餐",
    )
    place_words = (
        "哪一家店", "哪家店", "哪一家餐厅", "哪家餐厅", "哪家馆子", "哪家好吃",
        "去哪一家", "去哪家", "去哪里吃", "去哪儿吃", "去哪吃",
        "推荐一家店", "推荐一家餐厅", "找一家店", "去一家店", "附近", "店",
    )
    switch_words = (
        "换一家", "换一家店", "换家店", "换店", "换一批", "下一家",
        "再来一家", "重新找一家", "再换一家",
    )
    if any(marker in text for marker in cooking_markers):
        return False
    if any(marker in text for marker in restaurant_markers):
        return True
    if any(word in text for word in signature_words) and any(
        ctx in text for ctx in restaurant_context
    ):
        return True
    if any(word in text for word in occasion_words) and any(
        word in text for word in place_words
    ):
        return True
    if has_prior_restaurant and any(word in text for word in switch_words):
        return True
    return False


_HOME_SERVICE_STRONG = (
    "到家服务", "厨师到家", "私厨到家", "上门私厨", "私厨上门",
    "请厨师", "预约厨师", "请个厨师", "找个厨师", "上门做菜", "上门做饭",
)
_HOME_SERVICE_COOKING = (
    "做饭", "做菜", "烧菜", "下厨", "厨师", "私厨", "煮饭", "做顿饭", "做一桌", "上门服务",
)
_NON_CATERING_UPSTREAM = (
    "维修", "安装", "取件", "送货", "快递", "拜访", "体检", "保修", "售后",
    "保洁", "清洗", "家政", "搬家", "测量", "拍照",
)


def looks_like_home_service_request(text: str) -> bool:
    """识别真实的厨师上门需求，避免“上门维修/私厨菜”等语境误触发。"""
    raw = str(text or "")
    if not raw:
        return False
    if any(marker in raw for marker in _HOME_SERVICE_STRONG):
        return True
    if "上门" not in raw:
        return False
    has_cooking = any(word in raw for word in _HOME_SERVICE_COOKING)
    if any(word in raw for word in _NON_CATERING_UPSTREAM) and not has_cooking:
        return False
    return has_cooking


def is_recipe_change_request(text: str) -> bool:
    """识别换一道或调整菜品方向，避免被当作给上一道补图。"""
    text = str(text or "")
    broad_change_words = (
        "没胃口", "不想吃这个", "不想吃了", "换一道", "换一个", "换别的", "没食欲",
    )
    if any(word in text for word in broad_change_words):
        return True
    change_words = ("换成", "改成", "做成", "换做", "改做", "改为", "变成")
    recipe_words = ("面", "汤", "菜", "饭", "粥", "粉", "肉", "鱼", "鸡", "牛", "虾", "豆腐")
    return any(word in text for word in change_words) and any(
        word in text for word in recipe_words
    )


def classify_turn_intent(
    text: str,
    *,
    is_specific_dish: bool = False,
    execute_plan: bool = False,
    candidate_index: int | None = None,
    has_prior_recipe: bool = False,
    has_prior_candidates: bool = False,
    has_prior_restaurant: bool = False,
    is_candidate_revision: bool = False,
    has_recipe_index_ref: bool = False,
    mentions_recent_recipe: bool = False,
    mentions_recent_candidate: bool = False,
    recommendation_count: bool = False,
    allow_ordinal_confirmation: bool = False,
) -> str:
    """统一的路由层与 Agent 层意图分类入口。

    路由层没有候选/卡片上下文，只传文本级信号；Agent 层补充历史锚点参数。
    两边共用同一顺序和词表，避免同一句话在不同层被判成不同产品形态。
    """
    current = str(text or "").strip()
    if not current:
        return "other"
    if looks_like_home_service_request(current):
        return "home_service"
    if is_restaurant_ordering_scene(
        current,
        has_prior_restaurant=has_prior_restaurant,
    ):
        return "restaurant"
    if is_candidate_revision:
        return "recommend"
    if is_recipe_change_request(current):
        return "change_one"
    if execute_plan:
        return "confirm_one"
    if has_prior_candidates and candidate_index:
        return "confirm_one"

    confirm_words = ("就做", "就吃", "来这个", "做这个", "吃这个", "定这个", "选这个", "就它", "就这道")
    if any(word in current for word in confirm_words) or (
        has_recipe_index_ref
        and (has_prior_recipe or has_prior_candidates)
        and (mentions_recent_recipe or mentions_recent_candidate)
    ):
        return "confirm_one"
    if allow_ordinal_confirmation and any(
        word in current for word in ("第一道", "第二道", "第三道")
    ):
        return "confirm_one"

    followup_words = (
        "清淡", "少盐", "少油", "不要", "别放", "能不能", "可以吗", "适合吗",
        "热量", "钠", "糖", "脂肪", "怎么吃",
    )
    if (
        (has_prior_recipe or has_prior_candidates)
        and not mentions_recent_recipe
        and any(word in current for word in followup_words)
    ):
        return "followup"

    adjust_words = (
        "清淡", "少", "淡", "不要", "别", "盐", "油", "热量", "钠", "糖",
        "脂肪", "能不能", "可以吗", "适合吗", "怎么吃",
    )
    if (
        has_prior_candidates
        and mentions_recent_candidate
        and len(current) <= 14
        and not any(word in current for word in adjust_words)
    ):
        return "confirm_one"

    if recommendation_count:
        return "recommend"
    if is_specific_dish or looks_like_dining_request(current):
        return "recommend"
    return "other"


@dataclass(frozen=True)
class TurnDecision:
    intent: str = "other"
    picked_candidate: str = ""
    image_requested: bool = False
    image_reason: str = ""
    target_record_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_value(cls, value: Any) -> "TurnDecision | None":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            return None
        target = value.get("target_record_id")
        try:
            target = int(target) if target is not None else None
        except (TypeError, ValueError):
            target = None
        return cls(
            intent=str(value.get("intent") or "other"),
            picked_candidate=str(value.get("picked_candidate") or ""),
            image_requested=bool(value.get("image_requested")),
            image_reason=str(value.get("image_reason") or ""),
            target_record_id=target,
        )


TURN_DECISION_METADATA_KEY = "turn_decision"


def attach_turn_decision(message, decision: TurnDecision):
    """写入 LangChain 消息元数据，不污染用户可见正文。"""
    # 保留对旧测试替身/兼容调用的支持；真实 LangChain 消息具备该属性。
    if not hasattr(message, "additional_kwargs"):
        return message
    extra = dict(getattr(message, "additional_kwargs", None) or {})
    extra[TURN_DECISION_METADATA_KEY] = decision.to_dict()
    message.additional_kwargs = extra
    return message


def read_turn_decision(message) -> TurnDecision | None:
    extra = getattr(message, "additional_kwargs", None) or {}
    return TurnDecision.from_value(extra.get(TURN_DECISION_METADATA_KEY))
