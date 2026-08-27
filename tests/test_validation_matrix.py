import unittest

from benchmarks.validation import build_matrix, validate_host_receipt


class HostReceiptContractTest(unittest.TestCase):
    def _receipt(self, **overrides):
        receipt = {
            "source": "tools/result",
            "command": "python tests/run_all.py",
            "exitCode": 0,
            "receipt_id": "r-1",
            "session_id": "s-1",
            "call_id": "c-1",
            "timedOut": False,
            "sandboxDenied": False,
        }
        receipt.update(overrides)
        return receipt

    def test_model_like_claim_is_rejected(self):
        result = validate_host_receipt(
            {"source": "model", "command": "python tests/run_all.py", "exitCode": 0},
            "python tests/run_all.py",
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["source_ok"])
        self.assertIn("receipt_id", result["missing"])

    def test_exact_host_receipt_passes(self):
        result = validate_host_receipt(self._receipt(), "python tests/run_all.py")
        self.assertTrue(result["passed"])

    def test_replayed_receipt_fails(self):
        result = validate_host_receipt(
            self._receipt(), "python tests/run_all.py", consumed_ids={"r-1"}
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["already_consumed"])

    def test_nonzero_timeout_and_command_replay_fail(self):
        for receipt in (
            self._receipt(command="wrong"),
            self._receipt(exitCode=1),
            self._receipt(timedOut=True),
        ):
            self.assertFalse(validate_host_receipt(receipt, "python tests/run_all.py")["passed"])

    def test_matrix_ship_requires_all_receipts(self):
        good = validate_host_receipt(self._receipt(), "python tests/run_all.py")
        bad = validate_host_receipt(
            self._receipt(receipt_id="r-2", source="model"), "python tests/run_all.py"
        )
        matrix = build_matrix([good, bad])
        self.assertEqual(matrix["passed"], 1)
        self.assertFalse(matrix["can_ship"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
