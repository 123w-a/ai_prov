"""结构化整理节点关思考的回归：预算被 reasoning 吃空的病根不能复发。

背景：deepseek 把 reasoning token 计进 completion_tokens（即 max_tokens），
2048 预算下思考会吃光预算、content 变空，ChefAnswer 解析失败后全量重跑。
见 ``agent/chains.py::_structure_extra_body``。
"""

import os
import unittest
from unittest.mock import patch

from agent import chains


class StructureExtraBodyTest(unittest.TestCase):
    def test_deepseek_defaults_to_thinking_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRUCTURE_THINKING", None)
            self.assertEqual(
                chains._structure_extra_body("deepseek"),
                {"thinking": {"type": "disabled"}},
            )

    def test_explicit_on_restores_thinking(self):
        with patch.dict(os.environ, {"STRUCTURE_THINKING": "on"}):
            self.assertIsNone(chains._structure_extra_body("deepseek"))

    def test_non_deepseek_provider_never_gets_the_param(self):
        """中转站/其它厂商不接受该参数，带上会 400。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRUCTURE_THINKING", None)
            self.assertIsNone(chains._structure_extra_body("gpt"))


class StructureLlmWiringTest(unittest.TestCase):
    def setUp(self):
        self._saved = chains.structure_llm
        chains.structure_llm = None

    def tearDown(self):
        chains.structure_llm = self._saved

    def _build(self, provider):
        with patch(
            "infrastructure.model_name.resolve_provider", return_value=provider
        ), patch("infrastructure.model_name.get_langchain_llm") as factory:
            chains._get_structure_llm()
        return factory

    def test_deepseek_structure_llm_disables_thinking(self):
        factory = self._build("deepseek")

        self.assertEqual(
            factory.call_args.kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(
            factory.call_args.kwargs["max_tokens"], chains.STRUCTURE_MAX_TOKENS
        )
        # 实测上限：关思考后最重的一份「四菜家宴」要 2589~2734 token，
        # 旧的 2048 会 finish_reason=length 截断，因此下限必须高于它。
        self.assertGreaterEqual(chains.STRUCTURE_MAX_TOKENS, 4096)

    def test_other_provider_structure_llm_sends_no_extra_body(self):
        factory = self._build("gpt")

        self.assertIsNone(factory.call_args.kwargs["extra_body"])

    def test_structure_provider_stays_pinned_to_deepseek_by_default(self):
        """不写 STRUCTURE_PROVIDER 时结构化链不吃 CHEF_PROVIDER 的切换（原语义）。"""
        with patch.dict(os.environ, {}, clear=False), patch(
            "infrastructure.model_name.resolve_provider", return_value="deepseek"
        ) as resolve, patch("infrastructure.model_name.get_langchain_llm"):
            os.environ.pop("STRUCTURE_PROVIDER", None)
            chains._get_structure_llm()

        self.assertEqual(resolve.call_args.args[0], "deepseek")

    def test_explicit_structure_provider_is_honoured(self):
        with patch.dict(os.environ, {"STRUCTURE_PROVIDER": "gpt"}), patch(
            "infrastructure.model_name.resolve_provider", return_value="gpt"
        ) as resolve, patch("infrastructure.model_name.get_langchain_llm"):
            chains._get_structure_llm()

        self.assertEqual(resolve.call_args.args[0], "gpt")


class ExtraBodyPassthroughTest(unittest.TestCase):
    def test_get_langchain_llm_forwards_extra_body(self):
        from infrastructure.model_name import get_langchain_llm

        model = get_langchain_llm(
            "deepseek",
            api_key="test",
            base_url="https://api.deepseek.com",
            extra_body={"thinking": {"type": "disabled"}},
        )

        self.assertEqual(model.extra_body, {"thinking": {"type": "disabled"}})

    def test_omitted_extra_body_stays_unset(self):
        from infrastructure.model_name import get_langchain_llm

        model = get_langchain_llm(
            "deepseek",
            api_key="test",
            base_url="https://api.deepseek.com",
        )

        self.assertIsNone(model.extra_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
