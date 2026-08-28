"""AI 增强回填：确定性分类 + 备份回写语义。LLM 调用本身不做单测。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import answer_backfill as ab


class ClassifyTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(ab.classify_answer("__pending__"), "pending")
        self.assertEqual(ab.classify_answer(""), "pending")
        self.assertEqual(
            ab.classify_answer("（本轮回答未能完成：上游模型超时）"), "pending"
        )
        self.assertEqual(ab.classify_answer("短句追问：血压多少？"), "short")
        self.assertEqual(ab.classify_answer("x" * 320), "candidate")


class WriteBackTest(unittest.TestCase):
    def test_write_back_keeps_original_once(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        session = {
            "session_id": "bf_s1", "title": "t", "created_at": "12:00",
            "messages": [
                {"id": 1, "user_text": "q", "answer": "OLD " * 100},
            ],
        }
        with patch.object(ab, "_read_file", lambda p: json.dumps(session)), patch.object(
            ab, "_read_session", lambda sid: session
        ), patch.object(ab, "_write_session", lambda data: None):
            self.assertTrue(ab.write_back("bf_s1", 1, "NEW"))
            self.assertEqual(session["messages"][0]["answer"], "NEW")
            self.assertEqual(
                session["messages"][0]["answer_original"], "OLD " * 100
            )
            # 二次回写不覆盖首次备份
            ab.write_back("bf_s1", 1, "NEWER")
            self.assertEqual(
                session["messages"][0]["answer_original"], "OLD " * 100
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
