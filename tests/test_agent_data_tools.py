"""Agent 数据查询工具（查冰箱/查周报）的确定性回归测试。"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import agent_tools
from api.routes import fridge_route, reports_route
from agent_tools import (
    fridge_gap,
    healthy_remix,
    nearby_food,
    query_fridge_inventory,
    query_weekly_report,
    tools,
)


class AgentDataToolsTest(unittest.TestCase):
    def test_both_data_tools_registered(self):
        names = {t.name for t in tools}

        self.assertIn("query_fridge_inventory", names)
        self.assertIn("query_weekly_report", names)

    def test_fridge_tool_reports_missing_file_honestly(self):
        with patch.object(fridge_route, "_FILE", Path(__file__).with_name(".no-fridge.json")):
            payload = json.loads(query_fridge_inventory.func())

        self.assertEqual(payload["items"], [])
        self.assertIn("暂无记录", payload["note"])

    def test_fridge_tool_lists_recorded_items(self):
        path = Path(__file__).with_name(".agent-fridge-test.json")
        path.write_text(json.dumps({"items": ["鸡蛋", "西兰花"]}, ensure_ascii=False), encoding="utf-8")
        try:
            with patch.object(fridge_route, "_FILE", path):
                payload = json.loads(query_fridge_inventory.func())
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(payload["items"], ["鸡蛋", "西兰花"])

    def test_weekly_tool_admits_empty_history(self):
        with patch.object(reports_route, "_MEALS", Path(__file__).with_name(".no-meals.json")):
            payload = json.loads(query_weekly_report.func())

        self.assertFalse(payload["has_data"])
        self.assertIn("还没有饮食记录", payload["note"])

    def test_weekly_tool_briefs_dishes_trends_and_tips(self):
        path = Path(__file__).with_name(".agent-meals-test.json")
        now = datetime.now()
        ts = lambda days_ago: (now - timedelta(days=days_ago)).isoformat(timespec="seconds")
        records = [
            {"ts": ts(2), "session": "s1", "dish": "番茄炒蛋",
             "lights": ["钠:green"], "guardrails": 0},
            {"ts": ts(1), "session": "s1", "dish": "番茄炒蛋",
             "lights": ["钠:yellow"], "guardrails": 1},
            {"ts": ts(0), "session": "s1", "dish": "青椒肉丝",
             "lights": ["糖:green"], "guardrails": 0},
        ]
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        try:
            with patch.object(reports_route, "_MEALS", path):
                payload = json.loads(query_weekly_report.func())
        finally:
            path.unlink(missing_ok=True)

        self.assertTrue(payload["has_data"])
        self.assertEqual(payload["meals"], 3)
        self.assertIn("番茄炒蛋", payload["top_dishes"])
        self.assertIn(payload["light_trends"]["钠"], {"在好转", "在抬头", "保持平稳", "样本不足"})
        self.assertIsInstance(payload["recommendations"], list)

    def test_nearby_tool_filters_allergens(self):
        sample = [
            {
                "name": "芝麻酱拌面",
                "cuisine": "面食",
                "avg_price": 25,
                "distance_km": 0.5,
                "address": "示例路",
                "guardrail": "少油少盐",
            },
            {
                "name": "清蒸鸡腿饭",
                "cuisine": "家常菜",
                "avg_price": 30,
                "distance_km": 1.0,
                "address": "示例路",
                "guardrail": "点蒸煮炖",
            },
        ]
        with patch.object(agent_tools._legacy, "_amap_poi_search", return_value=sample), \
             patch.object(agent_tools._legacy, "_tool_allergens", return_value=["芝麻"]):
            payload = json.loads(nearby_food.func(city="益阳", district="赫山"))

        self.assertEqual(payload["count"], 1)
        self.assertIn("清蒸鸡腿饭", payload["text"])
        self.assertNotIn("芝麻酱拌面", payload["text"])
        self.assertEqual(payload["allergen_filter"]["removed"], 1)

    def test_healthy_remix_blocks_recipe_with_allergen(self):
        with patch.object(agent_tools._legacy, "_tool_allergens", return_value=["花生"]):
            payload = json.loads(healthy_remix.func("花生炖鸡：花生、鸡肉、盐"))

        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["swaps"], [])
        self.assertIn("花生及其制品", payload["allergen_filter"]["message"])

    def test_fridge_gap_blocks_recipe_with_allergen(self):
        with patch.object(agent_tools._legacy, "_tool_allergens", return_value=["花生"]):
            payload = json.loads(fridge_gap.func("花生炖鸡", "鸡肉、盐"))

        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["missing"], [])
        self.assertIn("花生及其制品", payload["allergen_filter"]["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
