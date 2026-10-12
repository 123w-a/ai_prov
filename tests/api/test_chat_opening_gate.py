"""chat_route 的 opening 覆盖闸门。

`_prefer_streamed_opening` 决定落库的 opening 用「模型给的」还是「已展示给用户的
流式正文」，此前零测试覆盖。agent/graph.py 为「本轮选定目标」校准过 opening 时会
下发 opening_authoritative，其契约原文就是「不得被旧流式正文覆盖」；前端
answerMerge.ts 已遵守，服务端必须一致，否则校准会在落库前被静默撤销、
而标志仍为 True，前端又拒绝改回。

本文件同时锁住普通轮次（无该标志）的既有替换行为不变。
"""

import unittest

from api.routes import chat_route

THRESHOLD = chat_route._STREAMED_OPENING_REPLACE_THRESHOLD
LONG_STREAMED = "正" * (THRESHOLD + 200)


class PreferStreamedOpeningTest(unittest.TestCase):
    def test_authoritative_opening_is_never_overwritten(self):
        answer = {"opening": "含花生的做法我不能推荐，已换成清炒时蔬。",
                  "opening_authoritative": True}
        before = answer["opening"]

        replaced = chat_route._prefer_streamed_opening(answer, LONG_STREAMED)

        self.assertFalse(replaced)
        self.assertEqual(answer["opening"], before)

    def test_authoritative_opening_survives_even_when_empty(self):
        # 标志为真即代表代码已接管本轮的 opening，不再接受流式正文回写。
        answer = {"opening": "", "opening_authoritative": True}
        self.assertFalse(chat_route._prefer_streamed_opening(answer, LONG_STREAMED))
        self.assertEqual(answer["opening"], "")

    def test_longer_streamed_text_still_wins_without_the_flag(self):
        # 既有行为：普通轮次里明显更长的流式正文仍要补进 opening，避免图片线程
        # 回填时把已经展示过的完整讲解覆盖成短兜底句。
        answer = {"opening": "好的。"}
        replaced = chat_route._prefer_streamed_opening(answer, LONG_STREAMED)
        self.assertTrue(replaced)
        self.assertEqual(answer["opening"], LONG_STREAMED)

    def test_empty_opening_is_filled_without_the_flag(self):
        answer = {"opening": ""}
        self.assertTrue(chat_route._prefer_streamed_opening(answer, "正文"))
        self.assertEqual(answer["opening"], "正文")

    def test_shorter_streamed_text_does_not_churn_opening(self):
        answer = {"opening": "这是一段已经很完整的讲解文本。"}
        self.assertFalse(chat_route._prefer_streamed_opening(answer, "短"))
        self.assertEqual(answer["opening"], "这是一段已经很完整的讲解文本。")

    def test_non_dict_answer_is_ignored(self):
        self.assertFalse(chat_route._prefer_streamed_opening(None, LONG_STREAMED))


class RetryStreamedOpeningTest(unittest.TestCase):
    def test_reset_discards_previous_generation_before_final_opening(self):
        first = "第一版完整讲解" * 200
        second = "第二版有效讲解"
        parts = []

        chat_route._accumulate_streamed_text(parts, "token", first)
        chat_route._accumulate_streamed_text(parts, "reset", True)
        chat_route._accumulate_streamed_text(parts, "token", second)

        streamed = "".join(parts)
        answer = {"opening": ""}
        self.assertTrue(chat_route._prefer_streamed_opening(answer, streamed))
        self.assertEqual(answer["opening"], second)
        self.assertNotIn(first, answer["opening"])

    def test_short_second_generation_does_not_replace_complete_opening(self):
        complete = "第二版完整安全讲解" * 20
        parts = []

        chat_route._accumulate_streamed_text(parts, "token", "第一版待丢弃内容")
        chat_route._accumulate_streamed_text(parts, "reset", True)
        chat_route._accumulate_streamed_text(parts, "token", "收到。")

        answer = {"opening": complete}
        self.assertFalse(
            chat_route._prefer_streamed_opening(answer, "".join(parts))
        )
        self.assertEqual(answer["opening"], complete)

    def test_non_token_events_do_not_change_streamed_text(self):
        parts = ["已有正文"]
        self.assertFalse(
            chat_route._accumulate_streamed_text(parts, "stage", "structuring")
        )
        self.assertEqual(parts, ["已有正文"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
