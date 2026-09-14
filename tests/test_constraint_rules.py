"""阶段 5 约束维度化：硬维拦截、软维只调整、联合矩阵。"""

import unittest

from constraint_rules import audit_constraint, build_member_adjustments, build_matrix


class ConstraintDimensionTest(unittest.TestCase):
    def test_allergen_is_hard_block(self):
        violations, adjustments = audit_constraint(
            "油焖大虾",
            [{"member": "爷爷", "dimension": "allergen", "value": "虾"}],
        )
        self.assertTrue(violations)
        self.assertEqual(violations[0]["dimension"], "allergen")
        self.assertEqual(adjustments, [])

    def test_chronic_is_hard_block_for_active_member(self):
        violations, _ = audit_constraint(
            "咸菜炒肉",
            [{"member": "爸爸", "dimension": "chronic", "value": "高血压"}],
        )
        self.assertTrue(violations)
        self.assertEqual(violations[0]["dimension"], "chronic")

    def test_texture_never_blocks_and_returns_adjustment(self):
        violations, adjustments = audit_constraint(
            "腰果鸡丁",
            [{"member": "爷爷", "dimension": "texture", "value": "软食"}],
        )
        self.assertEqual(violations, [])
        self.assertTrue(adjustments)
        self.assertIn("切小块", adjustments[0]["advice"])

    def test_energy_never_blocks_and_returns_portion_advice(self):
        violations, adjustments = audit_constraint(
            "红烧肉",
            [{"member": "弟弟", "dimension": "energy", "value": "减重"}],
        )
        self.assertEqual(violations, [])
        self.assertTrue(adjustments)
        self.assertIn("主食减半", adjustments[0]["advice"])

    def test_matrix_combines_hard_and_soft_dimensions(self):
        rows = build_matrix(
            ["腰果鸡丁", "清蒸鸡腿", "油焖大虾"],
            [
                {
                    "name": "爷爷",
                    "profile": {"allergens": ["虾"], "taste_notes": ["软食"]},
                },
                {"name": "弟弟", "profile": {"goal": "减重"}},
                {"name": "我", "profile": {}},
            ],
        )
        lookup = {(row["dish"], row["member"]): row["verdict"] for row in rows}
        self.assertEqual(lookup[("油焖大虾", "爷爷")], "不可吃")
        self.assertEqual(lookup[("油焖大虾", "弟弟")], "需调整")
        self.assertEqual(lookup[("油焖大虾", "我")], "可吃")
        self.assertEqual(lookup[("腰果鸡丁", "爷爷")], "需调整")
        self.assertEqual(lookup[("清蒸鸡腿", "弟弟")], "需调整")

    def test_member_adjustments_are_readable_and_member_specific(self):
        rows = build_matrix(
            ["腰果鸡丁", "清炒时蔬"],
            [
                {
                    "name": "爷爷",
                    "profile": {"taste_notes": ["软食"]},
                },
                {"name": "弟弟", "profile": {"goal": "减重"}},
            ],
        )
        lines = build_member_adjustments(rows)
        self.assertTrue(any(line.startswith("爷爷：") and "切小块" in line for line in lines))
        self.assertTrue(any(line.startswith("弟弟：") and "主食减半" in line for line in lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
