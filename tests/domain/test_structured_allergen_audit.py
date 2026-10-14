# -*- coding: utf-8 -*-
"""结构化过敏原审计前「否定提及剥离」的行为固化（2026-10-14 修复配套）。

背景：`structure_answer_node` 构造的结构化审计文本**没有**做任何剥离，导致模型在
步骤里写的安全提示（「不用花生油」「全程不含花生及其制品」）被当成真实下料
⇒ 卡片被放弃（recipes 空）⇒ 卡片与配图消失。

口径取舍（重要）：结构化审计是**过敏原安全底线**，因此使用保守版
`_strip_all_negated_lines()` —— 只有当该行内**每一处**过敏原提及都紧邻
纯否定词（不/别/勿/莫/禁/免/无/忌/避）时才整行剔除；只要有一处像真下料，
整行保留交审计。

**不能用**自由正文那版 `_strip_negated_allergen_mentions()`：它把「替/换/改」
也算警示并整行剔除，实测会把真下料漏掉（见本文件 KnownGapTest 的三条反例，
它们现在都必须判违规）。
"""

import unittest

from langchain_core.messages import AIMessage

from domain.allergen_rules import audit_allergens

import agent.graph as g

ALLERGEN = ["花生"]


def _stripped(text):
    return g._strip_all_negated_lines(text, ALLERGEN)


def _flagged(text):
    return bool(audit_allergens(_stripped(text), ALLERGEN, use_optional=True))


class WarningLineStrippedTest(unittest.TestCase):
    """整行都是安全提示 ⇒ 必须剔除，否则卡片被无谓放弃。"""

    def test_negated_usage_lines_are_stripped(self):
        for line in ("记住：不用花生油、不用花生调和油", "不加花生碎", "无花生油版",
                     "忌花生", "避免使用花生酱", "全程不含花生及其制品"):
            with self.subTest(line=line):
                self.assertEqual(_stripped(line), "")
                self.assertFalse(_flagged(line), line)


class RealUsageStillFlaggedTest(unittest.TestCase):
    """真下料必须照旧命中——剥离不能把硬审计放水。这是本文件的最高优先项。"""

    def test_amounted_ingredient_still_flagged(self):
        for line in ("花生油 2 勺", "花生碎 1 小把", "生抽、花生酱"):
            with self.subTest(line=line):
                self.assertTrue(_flagged(line), line)

    def test_switch_to_allergen_still_flagged(self):
        """★ 反例：『换成X』语义是改用，属真下料。自由正文那版会整行剥掉（漏判）。"""
        for line in ("换花生油", "改成花生碎", "替换成花生酱"):
            with self.subTest(line=line):
                self.assertTrue(_flagged(line), line)

    def test_mixed_line_keeps_real_usage(self):
        """★ 反例：同一行既有真下料又有警示，真下料必须留下并被判违规。"""
        self.assertTrue(_flagged("花生油 1 勺"))
        self.assertTrue(_flagged("糖醋排骨 花生油 1 勺 不用花生碎装饰"))

    def test_line_level_stripping_keeps_other_lines(self):
        text = "\n".join(["番茄炒蛋", "控盐版", "盐 1g", "记住：不用花生油", "打蛋炒番茄"])
        got = _stripped(text)
        self.assertNotIn("不用花生油", got)
        self.assertIn("盐 1g", got)
        self.assertIn("打蛋炒番茄", got)


class AcceptedFalsePositiveTest(unittest.TestCase):
    """已知取舍（**有意接受**）：『（替花生）』这类替代说明会被保留 → 误报一次。

    结构化路径宁可多误报（最多本轮不出卡片），也不接受漏判真下料。
    若要同时压住这一处，需要在自由正文那版的标记表上做语义区分
    （`替` 后紧跟 `成/为` 判为真下料），见 `.tmpdir/CODEX-REVIEW-brief.md` A2-2。
    """

    def test_substitution_note_is_kept_as_accepted_fp(self):
        self.assertEqual(_stripped("（替花生）"), "（替花生）")
        self.assertTrue(_flagged("（替花生）"))


class StructuredAuditTextShapeTest(unittest.TestCase):
    """审计文本必须保持逐行，否则按行剥离会把整道菜删掉（审计被放空）。"""

    def test_structured_text_lines_survive_stripping(self):
        lines = ["番茄炒蛋", "控盐版", "盐 1g", "打蛋", "记住：不用花生油", "炒番茄"]
        kept = _stripped("\n".join(lines))
        self.assertNotIn("不用花生油", kept)
        for must_keep in ("番茄炒蛋", "盐 1g", "打蛋", "炒番茄"):
            self.assertIn(must_keep, kept)


class FreeTextVersionIsLooserTest(unittest.TestCase):
    """固化两版口径的差异，防止有人「顺手统一」把保守版换回宽松版。"""

    def test_free_text_version_is_looser_than_structured_version(self):
        line = "花生油 1 勺，不用花生碎装饰"
        loose = g._strip_negated_allergen_mentions(line, ALLERGEN)
        strict = g._strip_all_negated_lines(line, ALLERGEN)
        self.assertEqual(loose, "", "自由正文那版会整行剥掉（这是既有行为，勿改）")
        self.assertEqual(strict, line, "结构化这版必须保留真下料行")


if __name__ == "__main__":
    unittest.main(verbosity=2)
