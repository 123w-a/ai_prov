# -*- coding: utf-8 -*-
"""A1 闭环：充分性追问答案 → restrict 候选 → confirm 写画像 → 下轮门控免重复追问。

链路：
    memory_candidates.extract_candidates（医嘱式控制强度，如「医生要求严格控盐」）
      → remember_candidates → confirm → profile.restricts
      → agent.graph._active_profile_restricts() → _intensity_sufficient() 为真
      → profile_gate_node 下一轮不再追问「该病种的控制强度」。

两步都在本文件断言：正向（写回后免追问）+ 反证（未写回时会追问）。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from agent.graph import _MISSING_INTENSITY, profile_gate_node
from api.routes import preferences_route as pr
from storage import memory_candidates


class SufficiencyWritebackClosureTest(unittest.TestCase):
    def test_ask_answer_writeback_stops_repeat_ask(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = tmp_path / "profile.json"
            candidates_file = tmp_path / "memory_candidates.json"
            family = {
                "version": 2,
                "active_id": "me",
                "members": [{"id": "me", "name": "我", "profile": {}}],
            }
            profile.write_text(json.dumps(family, ensure_ascii=False), encoding="utf-8")

            # 第 1 步：用户回答追问「医生要求严格控盐」→ 提取 restrict 候选并确认
            with patch.object(pr, "_DATA_DIR", tmp_path), patch.object(
                pr, "_PROFILE_PATH", profile
            ), patch.object(memory_candidates, "_DATA_DIR", tmp_path), patch.object(
                memory_candidates, "_CANDIDATES_PATH", candidates_file
            ):
                candidates = memory_candidates.extract_candidates(
                    "医生要求严格控盐，午饭吃什么",
                    family["members"],
                    active_member_id="me",
                )
                memory_candidates.remember_candidates(candidates)
                restrict = next(
                    c for c in candidates if c["dimension"] == "restrict"
                )
                memory_candidates.confirm(restrict["id"])

            saved = json.loads(profile.read_text(encoding="utf-8"))
            self.assertIn("严格控盐", saved["members"][0]["profile"]["restricts"])

            # 第 2 步：下一轮只提病种、不说强度 —— 画像已有硬限制，不得重复追问
            with patch("agent.graph._PROFILE_PATH", profile):
                out = profile_gate_node(
                    {"messages": [HumanMessage(content="我有高血压，午饭吃什么")]}
                )
            self.assertTrue(out["profile_ready"], out)
            self.assertEqual(out["profile_missing"], [])

    def test_without_writeback_gate_would_ask_intensity(self):
        """反证：同一句话在画像 restricts 为空时，门控会追问强度。"""
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile.json"
            family = {
                "version": 2,
                "active_id": "me",
                "members": [{"id": "me", "name": "我", "profile": {}}],
            }
            profile.write_text(json.dumps(family, ensure_ascii=False), encoding="utf-8")
            with patch("agent.graph._PROFILE_PATH", profile):
                out = profile_gate_node(
                    {"messages": [HumanMessage(content="我有高血压，午饭吃什么")]}
                )
            self.assertFalse(out["profile_ready"], out)
            self.assertEqual(out["profile_missing"], [_MISSING_INTENSITY])


if __name__ == "__main__":
    unittest.main(verbosity=2)
