# -*- coding: utf-8 -*-
"""答完追问回到候选清单 —— 回归固化（2026-10-14）。

问题：充分性门先追问后，泛推荐轮用户答完追问时本轮原话
不含求餐词（「我爸医生让严格控盐」）→ 被判成 other/闲聊 → 候选清单（两阶段点菜
第一跳、亮点①）不再出现。
修复：`_is_candidate_turn` 判定前先经 `_messages_before_last_ask()` 回到"追问之前"
的消息序列（含连续追问），让求餐意图与文本仍生效。
"""

import unittest

from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import _is_candidate_turn, _messages_before_last_ask

_ASK_INTENSITY = (
    "想确认一下：医生有没有给你具体的忌口要求（比如严格控盐、限嘌呤），"
    "还是平时注意一下就行？"
)
_ASK_PREGNANCY = "想确认一下：是已经怀孕了吗？如果已怀孕，现在孕几周了？我好按对应的营养要求来安排。"


class CandidateAfterAskTest(unittest.TestCase):
    def test_candidate_turn_after_answer_to_ask(self):
        """答完追问的那一轮仍算候选轮（否则候选清单再也不出现）。"""
        msgs = [
            HumanMessage(content="今晚一家三口吃什么？爸爸高血压，妈妈痛风，孩子花生过敏。"),
            AIMessage(content=_ASK_INTENSITY),
            HumanMessage(content="我爸医生让严格控盐，我妈控制得还行"),
        ]
        self.assertTrue(_is_candidate_turn(msgs))

    def test_plain_chitchat_without_ask_stays_non_candidate(self):
        """没有追问上下文时，纯闲聊（无求餐词）不得被当成候选轮。"""
        msgs = [HumanMessage(content="我爸医生让严格控盐，我妈控制得还行")]
        self.assertFalse(_is_candidate_turn(msgs))

    def test_chain_of_two_asks_still_returns_to_request(self):
        """连续两次追问后仍回到最初那条求餐消息。"""
        msgs = [
            HumanMessage(content="今晚一家三口吃什么？爸爸高血压"),
            AIMessage(content=_ASK_INTENSITY),
            HumanMessage(content="就是平时注意"),
            AIMessage(content=_ASK_PREGNANCY),
            HumanMessage(content="没有怀孕"),
        ]
        self.assertTrue(_is_candidate_turn(msgs))

    def test_topic_switch_after_ask_stays_non_candidate(self):
        """追问后换话题（信息问答 / 点名菜 / 做法追问）不得被当成候选轮。

        回归：截断不能无条件生效 —— 否则「红烧肉怎么做」「北京天气怎么样」
        会被当成"回答追问"，误推向候选清单形态。
        """
        for q in ("红烧肉怎么做", "北京天气怎么样", "什么是痛风"):
            with self.subTest(q=q):
                msgs = [
                    HumanMessage(content="今晚吃什么"),
                    AIMessage(content=_ASK_INTENSITY),
                    HumanMessage(content=q),
                ]
                self.assertFalse(_is_candidate_turn(msgs))

    def test_normal_recommend_turn_unaffected(self):
        self.assertTrue(_is_candidate_turn([HumanMessage(content="今晚一家三口吃什么？")]))

    def test_messages_before_last_ask_trims_only_ask_tail(self):
        msgs = [
            HumanMessage(content="推荐几道家常菜"),
            AIMessage(content=_ASK_INTENSITY),
            HumanMessage(content="清淡点就行"),
        ]
        trimmed = _messages_before_last_ask(msgs)
        self.assertEqual(len(trimmed), 1)
        self.assertEqual(str(trimmed[0].content), "推荐几道家常菜")

    def test_no_ask_tail_keeps_messages(self):
        msgs = [HumanMessage(content="推荐几道家常菜"), AIMessage(content="## 番茄炒蛋\n**食材**：番茄")]
        self.assertEqual(_messages_before_last_ask(msgs), msgs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
