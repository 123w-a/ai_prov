"""摘要模型分层回归：deepseek 可配置专用模型，其他 provider 不覆盖。"""

import os
import unittest
from unittest.mock import patch

import agent_graph


class SummaryFlashTest(unittest.TestCase):
    def test_deepseek_summary_uses_flash_name(self):
        with patch.dict(os.environ, {"SUMMARY_MODE_NAME": "deepseek-v4-flash"}):
            agent_graph.rebuild_llms(force_provider="deepseek")
            self.assertIn("flash", (agent_graph.summary_llm.model_name or "").lower())

    def test_other_provider_keeps_default_name(self):
        agent_graph.rebuild_llms(force_provider="gpt")
        self.assertNotIn("flash", (agent_graph.summary_llm.model_name or "").lower())
        # 恢复默认 provider，避免污染其他测试
        agent_graph.rebuild_llms()


if __name__ == "__main__":
    unittest.main(verbosity=2)
