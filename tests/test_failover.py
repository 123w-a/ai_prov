"""A 方案 failover 回归：provider 健康冷却、备用挑选、故障分类、重建幂等。"""

import unittest

from model_name import (
    _PROVIDER_COOLDOWN,
    _PROVIDER_COOLDOWN_SECONDS,
    _is_configured,
    is_provider_failure,
    mark_provider_down,
    pick_fallback_provider,
)


class ProviderFailureClassifyTest(unittest.TestCase):
    def test_timeout_error_names(self):
        class APITimeoutError(Exception):
            pass

        self.assertTrue(is_provider_failure(APITimeoutError("Request timed out.")))

    def test_message_fragments(self):
        self.assertTrue(is_provider_failure(RuntimeError("Connection was reset")))
        self.assertTrue(is_provider_failure(RuntimeError("Request TIMED OUT")))
        self.assertTrue(is_provider_failure(RuntimeError("Upstream request failed")))
        self.assertTrue(is_provider_failure(RuntimeError("Bad Gateway")))
        self.assertFalse(is_provider_failure(ValueError("bad json shape")))
        self.assertFalse(is_provider_failure(RuntimeError("rate limit 429")))


class ProviderCooldownTest(unittest.TestCase):
    def tearDown(self):
        _PROVIDER_COOLDOWN.clear()

    def test_mark_down_blocks_pick(self):
        providers = [n for n in ("gpt", "deepseek") if _is_configured(n)]
        if len(providers) < 2:
            self.skipTest("需要两个已配置 provider 才能验证备用挑选")
        first, second = providers[0], providers[1]

        mark_provider_down(first, seconds=120)
        self.assertEqual(pick_fallback_provider(exclude=None), second)
        mark_provider_down(second, seconds=120)
        self.assertIsNone(pick_fallback_provider(exclude=None))

    def test_cooldown_expires(self):
        from model_name import _provider_in_cooldown

        mark_provider_down("__fake__", seconds=-1)
        self.assertFalse(_provider_in_cooldown("__fake__"))
        self.assertEqual(_PROVIDER_COOLDOWN_SECONDS, 300)


class RebuildLlmsTest(unittest.TestCase):
    def test_rebuild_keeps_bound_tools_fresh(self):
        import agent_graph

        old_tools_obj = agent_graph.llm_with_tools
        agent_graph.rebuild_llms(agent_graph.provider)
        self.assertIsNot(agent_graph.llm_with_tools, old_tools_obj)
        self.assertIsNotNone(agent_graph.llm)
        self.assertIsNotNone(agent_graph.summary_llm)
        self.assertIsNotNone(agent_graph.retrieval_llm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
