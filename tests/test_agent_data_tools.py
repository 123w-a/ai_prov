"""Agent 数据查询工具（查冰箱/查周报）的确定性回归测试。"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from api.routes import fridge_route, reports_route
from agent_tools import query_fridge_inventory, query_weekly_report, tools


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
        records = [
            {"ts": "2026-08-25T12:00:00", "session": "s1", "dish": "番茄炒蛋",
             "lights": ["钠:green"], "guardrails": 0},
            {"ts": "2026-08-26T12:00:00", "session": "s1", "dish": "番茄炒蛋",
             "lights": ["钠:yellow"], "guardrails": 1},
            {"ts": "2026-08-27T12:00:00", "session": "s1", "dish": "青椒肉丝",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
