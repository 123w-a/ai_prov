"""失败图自动补图队列回归：扫描过滤、回写字段、冷却与幂等。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import image_retry_queue as q
import sessions_store


def _session(sid, messages):
    return {"session_id": sid, "title": sid, "created_at": "", "messages": messages}


class ImageRetryQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher_s = patch.object(sessions_store, "SESSIONS_DIR", self.dir)
        patcher_q = patch.object(q, "SESSIONS_DIR", self.dir)
        for p in (patcher_s, patcher_q):
            p.start()
            self.addCleanup(p.stop)
        q._cooldown.clear()

    def _write(self, sid, messages):
        fp = self.dir / f"{sid}.json"
        fp.write_text(
            json.dumps(_session(sid, messages), ensure_ascii=False), encoding="utf-8"
        )
        past = __import__("time").time() - q.RECENT_WRITE_GRACE_S - 60
        __import__("os").utime(fp, (past, past))  # 绕过『刚写入=生成中』的宽限期

    def test_scan_and_backfill_writes_image(self):
        self._write("s1", [
            {"id": 1, "user_text": "a", "answer": json.dumps({"recipes": [
                {"name": "番茄炒蛋", "image_url": None},
                {"name": "凉拌黄瓜", "image_url": "https://x/y.jpg"},
            ]}, ensure_ascii=False)},
            {"id": 2, "user_text": "b", "answer": "__pending__"},
        ])
        with patch.object(q, "find_recipe_image", lambda dish: ("https://oss/ai.png", "ai")):
            targets = q.scan_missing_images()
            self.assertEqual([(t[0], t[2]) for t in targets], [("s1", "番茄炒蛋")])
            self.assertEqual(q.backfill_once(), 1)
        data = json.loads((self.dir / "s1.json").read_text(encoding="utf-8"))
        rec = data["messages"][0]
        ans = json.loads(rec["answer"])
        self.assertEqual(ans["recipes"][0]["image_url"], "https://oss/ai.png")
        self.assertTrue(ans["recipes"][0]["image_ai_generated"])
        self.assertEqual(ans["recipes"][1]["image_url"], "https://x/y.jpg")  # 有图不被覆盖
        self.assertIn("后台自动补图", ans["image_note"])
        self.assertEqual(rec["image_url"], "https://oss/ai.png")

    def test_cooldown_prevents_repeat(self):
        self._write("s2", [
            {"id": 1, "user_text": "a", "answer": json.dumps({"recipes": [
                {"name": "白灼菜心", "image_url": None},
            ]}, ensure_ascii=False)},
        ])
        with patch.object(q, "find_recipe_image", lambda dish: (None, "none")):
            self.assertEqual(q.backfill_once(), 0)
            self.assertEqual(q.scan_missing_images(), [])  # 冷却期内不再选为目标

    def test_recent_session_file_skipped(self):
        fp = self.dir / "s3.json"
        fp.write_text(json.dumps(_session("s3", [
            {"id": 1, "user_text": "a", "answer": json.dumps({"recipes": [
                {"name": "热汤面", "image_url": None},
            ]}, ensure_ascii=False)},
        ]), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(q.scan_missing_images(), [])  # mtime 刚写入 → 视为生成中


if __name__ == "__main__":
    unittest.main(verbosity=2)
