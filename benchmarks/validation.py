"""Validation matrix contract for host-authoritative tools/result receipts.

This module validates identity fields, command binding, execution outcome, and
in-batch replay. Receipt authenticity is provided by the host tools/result
registry (for example speed-guard), not by caller-supplied JSON or this module.
"""
from __future__ import annotations

import hashlib
from typing import Any

REQUIRED_FIELDS = ("source", "command", "exitCode", "receipt_id", "session_id", "call_id")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def validate_host_receipt(
    receipt: dict[str, Any], expected_command: str, consumed_ids: set[str] | None = None
) -> dict[str, Any]:
    missing = [key for key in REQUIRED_FIELDS if key not in receipt or receipt.get(key) is None]
    receipt_id = _text(receipt.get("receipt_id"))
    already_consumed = bool(consumed_ids is not None and receipt_id in consumed_ids)
    source_ok = receipt.get("source") == "tools/result"
    command_ok = _text(receipt.get("command")) == expected_command
    exit_ok = receipt.get("exitCode") == 0
    timeout_ok = not bool(receipt.get("timedOut") or receipt.get("timeout"))
    sandbox_ok = not bool(receipt.get("sandboxDenied") or receipt.get("sandbox_denied"))
    passed = not missing and not already_consumed and source_ok and command_ok and exit_ok and timeout_ok and sandbox_ok
    return {
        "passed": passed,
        "receipt_id": receipt_id,
        "source": receipt.get("source"),
        "missing": missing,
        "already_consumed": already_consumed,
        "source_ok": source_ok,
        "command_ok": command_ok,
        "exit_ok": exit_ok,
        "timeout_ok": timeout_ok,
        "sandbox_ok": sandbox_ok,
        "receipt_hash": hashlib.sha256(repr(sorted(receipt.items())).encode()).hexdigest(),
    }


def build_matrix(results: list[dict[str, Any]]) -> dict[str, Any]:
    receipt_ids = [item.get("receipt_id") for item in results]
    duplicate_ids = sorted({rid for rid in receipt_ids if rid and receipt_ids.count(rid) > 1})
    required = len(results)
    passed = sum(1 for item in results if item.get("passed"))
    if duplicate_ids:
        passed = 0
    return {
        "schema": "ai-prov.validation-matrix.v1",
        "authority": "host:tools/result",
        "required": required,
        "passed": passed,
        "failed": required - passed,
        "duplicate_receipt_ids": duplicate_ids,
        "can_ship": required > 0 and passed == required and not duplicate_ids,
        "results": results,
    }
