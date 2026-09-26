"""本轮产品形态决策的确定性回归测试。"""

import unittest
import importlib.util
from pathlib import Path

from langchain_core.messages import HumanMessage


_MODULE_PATH = Path(__file__).resolve().parents[1] / "agent" / "turn_decision.py"
_SPEC = importlib.util.spec_from_file_location("turn_decision_under_test", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)

TurnDecision = _MODULE.TurnDecision
attach_turn_decision = _MODULE.attach_turn_decision
read_turn_decision = _MODULE.read_turn_decision


class TurnDecisionTest(unittest.TestCase):
    def _message(self, text, **values):
        message = HumanMessage(content=text)
        attach_turn_decision(message, TurnDecision(**values))
        return message

    def test_metadata_round_trip_keeps_record_target(self):
        decision = TurnDecision(
            intent="confirm_one",
            picked_candidate="番茄炒蛋",
            image_requested=True,
            image_reason="candidate_pick",
            target_record_id=42,
        )
        message = HumanMessage(content="选第1个")

        attach_turn_decision(message, decision)

        self.assertEqual(read_turn_decision(message), decision)

    def test_plain_string_test_double_is_left_untouched(self):
        message = "test-message"

        self.assertIs(attach_turn_decision(message, TurnDecision()), message)
        self.assertIsNone(read_turn_decision(message))


if __name__ == "__main__":
    unittest.main()
