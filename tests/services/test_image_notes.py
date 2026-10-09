import unittest

from services.image_notes import AI_IMAGE_NOTE, build_image_note, sanitize_image_note


class ImageNoteTest(unittest.TestCase):
    def test_ai_image_note_removes_old_no_image_conclusion(self):
        previous = (
            "AI 生成示意图：本次搜索结果里没有可用的「青菜炒鸡蛋」成品图"
            "（返回的图片链接均为空），所以不放图。"
            "口感描述：青菜脆嫩清甜，鸡蛋软香蓬松。"
        )

        note = build_image_note(True, previous)

        self.assertTrue(note.startswith(AI_IMAGE_NOTE))
        self.assertIn("口感描述：青菜脆嫩清甜", note)
        self.assertNotIn("没有可用", note)
        self.assertNotIn("图片链接均为空", note)
        self.assertNotIn("不放图", note)

    def test_missing_image_note_drops_only_failure_sentence(self):
        note = sanitize_image_note(
            "没有成品图，因此先不放图。口感偏清爽，适合少油做法。"
        )

        self.assertEqual(note, "口感偏清爽，适合少油做法。")

    def test_rebuilding_ai_note_is_idempotent(self):
        once = build_image_note(
            True,
            "后台自动补图；如与实际成品有出入，以文字描述为准",
        )
        twice = build_image_note(True, once)

        self.assertEqual(once, twice)
        self.assertEqual(once.count(AI_IMAGE_NOTE), 1)
        self.assertIn("如与实际成品有出入，以文字描述为准", once)
        self.assertNotIn("后台自动补图", once)


if __name__ == "__main__":
    unittest.main(verbosity=2)
