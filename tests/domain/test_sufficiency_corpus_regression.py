# -*- coding: utf-8 -*-
"""充分性门控 · 带标签语料回归（71 例，原 ``.tmpdir/sufficiency_calib`` 固化进测试套件）。

- **语料**：``sufficiency_corpus.json``（同目录）：25 正例 / 46 负例，覆盖
  symptom / intensity / allergen / population / pregnancy / combo / baseline / none 八组。
- **固定档案**：3 位在档成员（孩子 / 爸爸 / 妈妈），激活成员「孩子」已记录花生过敏。
- **统一门控**：逐例精确匹配期望追问键（校准口径 TP25 / FP0 / FN0 / TN46）。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from agent.graph import profile_gate_node

_DATA = Path(__file__).resolve().parent / "sufficiency_corpus.json"

def _write_profile(target: Path) -> Path:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))["_profile"]
    members = []
    for name in raw["members"]:
        profile = {}
        if name == raw["active_member"]:
            profile["allergens"] = list(raw["active_member_allergens"])
        members.append({"id": "m_" + name, "name": name, "profile": profile})
    target.write_text(
        json.dumps(
            {"version": 2, "active_id": "m_" + raw["active_member"], "members": members},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


class SufficiencyCorpusRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(_DATA.read_text(encoding="utf-8"))["cases"]
        cls._tmp = tempfile.TemporaryDirectory()
        cls.profile = _write_profile(Path(cls._tmp.name) / "profile.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _ask_key(self, text):
        with patch("agent.graph._PROFILE_PATH", self.profile):
            out = profile_gate_node({"messages": [HumanMessage(content=text)]})
        if out.get("profile_ready"):
            return ""
        return (out.get("profile_missing") or [""])[0]

    def test_corpus_size_guard(self):
        """语料防截断：71 例、25 正例 / 46 负例。"""
        self.assertEqual(len(self.cases), 71)
        positives = sum(1 for c in self.cases if c["expect"])
        self.assertEqual((positives, len(self.cases) - positives), (25, 46))

    def test_unified_gate_matches_all_labels(self):
        for case in self.cases:
            with self.subTest(case=case["id"], dim=case["dim"], text=case["text"]):
                self.assertEqual(self._ask_key(case["text"]), case["expect"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
