"""统一的本轮产品形态决策。"""

from dataclasses import asdict, dataclass
from typing import Any


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
