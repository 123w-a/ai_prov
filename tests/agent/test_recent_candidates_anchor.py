# -*- coding: utf-8 -*-
"""`_recent_candidates()` 候选锚点回溯的行为固化（2026-10-14 修复配套）。

背景：真机复现「泛推荐 → 就第1个吧」不出卡片。根因是反向扫描时
**一遇到非候选 payload 的 AIMessage 就 return []**，而真实会话里候选与
选定轮之间夹着 RAG 结果、联网结果、空工具占位消息 ⇒ 永远够不到候选
⇒ `structure_answer_node` 判定「序号指代失效」返回空 ⇒ 卡片与配图消失。

本文件同时守住**不能放宽**的那一侧：候选之后隔了真正的对话轮，
旧序号不得再算选定（冻结语义）。
"""

import json
import unittest

from langchain_core.messages import AIMessage, HumanMessage

import agent.graph as g


def _candidates(*names):
    return AIMessage(
        content=json.dumps(
            {"answer_kind": "candidates", "candidates": list(names)}, ensure_ascii=False
        )
    )


def _tool_json(**payload):
    return AIMessage(content=json.dumps(payload, ensure_ascii=False))


NAMES = ["清蒸鲈鱼", "西兰花炒鸡胸肉", "番茄豆腐煲"]


class CandidateAnchorTest(unittest.TestCase):
    def test_adjacent_candidate_found(self):
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)

    def test_candidate_behind_rag_result_still_found(self):
        """★ 本次修复点：RAG 检索结果夹在中间，不能把候选锚点判失效。"""
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                _tool_json(found=True, hits=[{"source": "成人高血压食养指南.pdf"}]),
                HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)

    def test_candidate_behind_web_search_result_still_found(self):
        """★ 本次修复点：联网结果同理。"""
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                _tool_json(text="1. 标题：清蒸鲈鱼的做法"), _tool_json(query="清蒸鲈鱼"),
                HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)

    def test_candidate_behind_empty_tool_message_still_found(self):
        """★ 本次修复点：工具调用后的空 AIMessage 占位是同轮内部噪声。"""
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                AIMessage(content=""), HumanMessage(content="1吧")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)

    def test_candidate_behind_tool_calls_message_still_found(self):
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                AIMessage(content="", tool_calls=[{
                    "name": "nutrition_kb_search",
                    "args": {"query": "低盐"},
                    "id": "call_probe_1",
                }]),
                HumanMessage(content="第2道")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)

    def test_body_text_after_candidate_stops_lookup(self):
        """冻结语义：候选之后隔了真正的对话轮，旧序号不能再算选定。"""
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                HumanMessage(content="换点别的"), AIMessage(content="## 番茄炒蛋\n食材：…"),
                HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), [])

    def test_recipe_card_after_candidate_stops_lookup(self):
        """已交付过结构化卡 ⇒ 也属隔轮，不再往回认旧候选。"""
        card = AIMessage(content=json.dumps(
            {"recipes": [{"name": "番茄炒蛋", "intro": "x", "difficulty": 2,
                          "nutrition": 4, "steps": ["a"]}]}, ensure_ascii=False))
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                HumanMessage(content="做番茄炒蛋"), card, HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), [])

    def test_no_candidate_returns_empty(self):
        msgs = [HumanMessage(content="中午吃什么")]
        self.assertEqual(g._recent_candidates(msgs), [])

    def test_internal_turns_do_not_truncate_history(self):
        """历史摘要 / 护栏审核是内部假用户消息，不算新轮次，不能截断回溯。"""
        msgs = [HumanMessage(content="晚餐推荐几道菜"), _candidates(*NAMES),
                HumanMessage(content="[历史对话摘要，供参考]\n- 口味：控盐"),
                HumanMessage(content="就第1个吧")]
        self.assertEqual(g._recent_candidates(msgs), NAMES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
