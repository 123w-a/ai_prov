"""阶段 6 画像候选：临时表达过滤、确认写入、候选清理。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.routes import preferences_route as pr
import memory_candidates


class MemoryCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.profile_path = self.data_dir / "profile.json"
        self.candidates_path = self.data_dir / "memory_candidates.json"
        self.patches = [
            patch.object(pr, "_DATA_DIR", self.data_dir),
            patch.object(pr, "_PROFILE_PATH", self.profile_path),
            patch.object(memory_candidates, "_DATA_DIR", self.data_dir),
            patch.object(memory_candidates, "_CANDIDATES_PATH", self.candidates_path),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _members(self):
        return [
            {"id": "grandpa", "name": "爷爷", "profile": {}},
            {"id": "brother", "name": "弟弟", "profile": {}},
        ]

    def test_temporary_expression_does_not_create_candidate(self):
        candidates = memory_candidates.extract_candidates(
            "我爷爷今天想吃辣",
            self._members(),
        )
        self.assertEqual(candidates, [])

    def test_persistent_expression_has_source_and_pending_status(self):
        candidates = memory_candidates.extract_candidates(
            "我爷爷咬不动硬的",
            self._members(),
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["member"], "爷爷")
        self.assertEqual(candidates[0]["dimension"], "texture")
        self.assertEqual(candidates[0]["source_text"], "我爷爷咬不动硬的")
        memory_candidates.remember_candidates(candidates)
        self.assertEqual(memory_candidates.get_pending()[0]["status"], "pending")

    def test_confirm_writes_profile_deterministically(self):
        family = {
            "version": 2,
            "active_id": "grandpa",
            "members": [
                {"id": "grandpa", "name": "爷爷", "profile": {}},
                {"id": "brother", "name": "弟弟", "profile": {}},
            ],
        }
        self.profile_path.write_text(
            json.dumps(family, ensure_ascii=False),
            encoding="utf-8",
        )
        candidates = memory_candidates.extract_candidates(
            "爷爷对花生过敏",
            self._members(),
        )
        memory_candidates.remember_candidates(candidates)
        confirmed = memory_candidates.confirm(candidates[0]["id"])
        self.assertEqual(confirmed["status"], "confirmed")
        saved = json.loads(self.profile_path.read_text(encoding="utf-8"))
        grandpa = next(item for item in saved["members"] if item["id"] == "grandpa")
        self.assertIn("花生", grandpa["profile"]["allergens"])

    def test_multiple_allergens_in_one_statement_are_all_candidates(self):
        candidates = memory_candidates.extract_candidates(
            "我对花生和芝麻过敏",
            [{"id": "me", "name": "我", "profile": {}}],
            session_id="s-allergens",
        )
        self.assertCountEqual(
            [(item["dimension"], item["value"], item["severity"]) for item in candidates],
            [
                ("allergen", "花生", "hard"),
                ("allergen", "芝麻", "hard"),
            ],
        )

    def test_dismiss_is_permanent_and_clear_removes_member_rows(self):
        candidates = memory_candidates.extract_candidates(
            "爷爷不能吃辣",
            self._members(),
        )
        memory_candidates.remember_candidates(candidates)
        self.assertTrue(memory_candidates.dismiss(candidates[0]["id"]))
        self.assertEqual(memory_candidates.get_pending(), [])

    def test_session_isolated_and_medical_restriction_is_hard(self):
        candidates = memory_candidates.extract_candidates(
            "医生要求爷爷不能吃辣",
            self._members(),
            session_id="s-grandpa",
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["dimension"], "restrict")
        self.assertEqual(candidates[0]["severity"], "hard")
        memory_candidates.remember_candidates(candidates)
        self.assertEqual(len(memory_candidates.get_pending("s-grandpa")), 1)
        self.assertEqual(memory_candidates.get_pending("other-session"), [])

    def test_existing_profile_value_is_not_proposed_again(self):
        members = [
            {
                "id": "grandpa",
                "name": "爷爷",
                "profile": {"taste_notes": ["不吃辣"]},
            }
        ]
        self.assertEqual(
            memory_candidates.extract_candidates("爷爷不能吃辣", members, session_id="s1"),
            [],
        )

    def test_once_does_not_write_profile_and_stops_pending(self):
        candidates = memory_candidates.extract_candidates(
            "爷爷不能吃辣",
            self._members(),
            session_id="s1",
        )
        memory_candidates.remember_candidates(candidates)
        once = memory_candidates.remember_once(candidates[0]["id"])
        self.assertEqual(once["status"], "once")
        self.assertEqual(memory_candidates.get_pending("s1"), [])
        self.assertEqual(memory_candidates.render_pending_constraints("s1").count("爷爷"), 1)
        self.assertFalse(self.profile_path.exists())
        self.assertEqual(
            memory_candidates.clear_member_candidates("grandpa", "爷爷"),
            1,
        )
        self.assertEqual(memory_candidates.get_pending(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
