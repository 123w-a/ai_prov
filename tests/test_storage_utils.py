"""本地 JSON/文本存储的原子写入回归测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_utils import atomic_write_json, atomic_write_text


class StorageUtilsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_text_write_creates_parent_and_backup(self):
        target = self.root / "nested" / "preferences.txt"
        atomic_write_text(target, "第一版\n")
        atomic_write_text(target, "第二版\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "第二版\n")
        self.assertEqual(
            target.with_name("preferences.txt.bak").read_text(encoding="utf-8"),
            "第一版\n",
        )

    def test_json_write_is_valid_and_backup_keeps_previous_value(self):
        target = self.root / "state.json"
        atomic_write_json(target, {"version": 1, "items": ["a"]})
        atomic_write_json(target, {"version": 2, "items": ["b"]})

        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8")),
            {"version": 2, "items": ["b"]},
        )
        self.assertEqual(
            json.loads(target.with_name("state.json.bak").read_text(encoding="utf-8")),
            {"version": 1, "items": ["a"]},
        )

    def test_replace_failure_does_not_damage_existing_file(self):
        target = self.root / "state.json"
        target.write_text('{"version": 1}', encoding="utf-8")

        with patch("storage_utils.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                atomic_write_json(target, {"version": 2})

        self.assertEqual(target.read_text(encoding="utf-8"), '{"version": 1}')
        self.assertEqual(list(self.root.glob(".state.json.*.tmp")), [])

    def test_backup_can_be_disabled(self):
        target = self.root / "state.json"
        atomic_write_json(target, {"version": 1})
        atomic_write_json(target, {"version": 2}, backup=False)

        self.assertFalse(target.with_name("state.json.bak").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
