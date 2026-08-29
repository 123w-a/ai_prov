"""口味信号：词典检测 / 计数窗口 / 建议阈值 / taste-note 沉淀。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api.routes.preferences_route as pr
from taste_store import detect_tastes, record_signals, suggest


class DetectTest(unittest.TestCase):
    def test_lexicon_hits(self):
        self.assertEqual(detect_tastes("水煮鱼片"), ["辣"])
        self.assertIn("辣", detect_tastes("麻辣香锅配红烧肉"))
        self.assertIn("油腻", detect_tastes("麻辣香锅配红烧肉"))
        self.assertEqual(detect_tastes("清蒸鲈鱼"), [])
        self.assertEqual(detect_tastes(""), [])


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "taste_signals.json"

    def test_threshold_and_window(self):
        with patch("taste_store._LOG", self.log):
            self.assertIsNone(suggest())
            record_signals(["辣"], "s1", 1)   # 1 次 → 不够阈值
            self.assertIsNone(suggest(min_count=2))
            record_signals(["辣", "油腻"], "s2", 2)
            sug = suggest(min_count=2)
            self.assertIsNotNone(sug)
            self.assertEqual(sug["taste"], "辣")
            self.assertEqual(sug["count"], 2)
            self.assertEqual(sug["note_label"], "不吃辣")


class TasteNoteEndpointTest(unittest.TestCase):
    def test_add_note_dedup(self):
        family = {
            "active_id": "m_a",
            "members": [
                {"id": "m_a", "name": "小美", "profile": {"taste_notes": []}},
            ],
        }
        with patch.object(pr, "_read_family", lambda: family), patch.object(
            pr, "_write_family", lambda data: None
        ):
            r1 = pr.add_taste_note(pr.TasteNotePayload(text="不吃辣"))
            self.assertTrue(r1["data"]["added"])
            r2 = pr.add_taste_note(pr.TasteNotePayload(text="不吃辣"))
            self.assertFalse(r2["data"]["added"])
            self.assertEqual(family["members"][0]["profile"]["taste_notes"], ["不吃辣"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
