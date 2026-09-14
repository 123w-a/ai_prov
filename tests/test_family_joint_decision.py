"""阶段 3 联合决策闭环：共同主菜、成员调整和逐人安全矩阵。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_graph
import main
from agent_chains import rank_recipes
from agent_schemas import ChefAnswer, DishMatrixItem, Recipe


def _recipe(name):
    return Recipe(
        name=name,
        intro="家常快手菜",
        difficulty=1,
        nutrition=4,
        steps=["小火加热至熟透。"],
    )


class FamilyJointDecisionTest(unittest.TestCase):
    def _family_members(self):
        return [
            {
                "name": "爷爷",
                "profile": {"allergens": ["虾"], "taste_notes": ["软食"]},
            },
            {"name": "弟弟", "profile": {"goal": "减重"}},
        ]

    def test_default_ranking_keeps_only_one_common_dish(self):
        answer = ChefAnswer(
            recipes=[_recipe("清蒸鸡腿"), _recipe("蒜蓉青菜")],
            member_adjustments=[
                "给爷爷：切小块并延长蒸制时间。",
                "给爸爸：不额外淋糖，主食减三分之一。",
            ],
            dish_matrix=[
                DishMatrixItem(
                    dish="清蒸鸡腿",
                    member="爷爷",
                    verdict="需调整",
                    reason="软食处理",
                )
            ],
        )
        ranked = rank_recipes(answer, allow_multiple=False)
        self.assertEqual(len(ranked.recipes), 1)
        self.assertEqual(len(ranked.member_adjustments), 2)
        self.assertEqual(ranked.dish_matrix[0].verdict, "需调整")

    def test_inactive_member_allergen_does_not_union_block_main_dish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "data" / "profile.json"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "active_id": "me",
                        "members": [
                            {
                                "id": "me",
                                "name": "我",
                                "profile": {"conditions": ["高血压"]},
                            },
                            {
                                "id": "grandpa",
                                "name": "爷爷",
                                "profile": {"allergens": ["虾"], "taste_notes": ["软食"]},
                            },
                            {
                                "id": "brother",
                                "name": "弟弟",
                                "profile": {"goal": "减重"},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(
                agent_graph,
                "__file__",
                str(profile_path.parent.parent / "agent_graph.py"),
            ):
                self.assertEqual(agent_graph._family_allergens(), ["虾"])
                self.assertEqual(agent_graph._allergens_for_audit(), [])

            with patch.object(main, "_PROFILE_PATH", profile_path):
                rendered = main.load_preferences()
            self.assertIn("健康画像·我", rendered)
            self.assertIn("同餐其他成员", rendered)
            self.assertIn("爷爷", rendered)
            self.assertIn("虾", rendered)
            self.assertIn("弟弟", rendered)
            self.assertIn("减重", rendered)

    def test_schema_fields_are_backward_compatible(self):
        answer = ChefAnswer(recipes=[_recipe("清炒时蔬")])
        self.assertEqual(answer.member_adjustments, [])
        self.assertEqual(answer.dish_matrix, [])

    def test_deterministic_matrix_overrides_model_guess(self):
        answer = ChefAnswer(
            recipes=[_recipe("腰果鸡丁")],
            member_adjustments=["模型猜测的内容不应保留。"],
            dish_matrix=[
                DishMatrixItem(
                    dish="腰果鸡丁",
                    member="错误成员",
                    verdict="可吃",
                    reason="模型自填",
                )
            ],
        )
        result = agent_graph._apply_family_differentiation(
            answer,
            self._family_members(),
        )
        self.assertEqual(len(result.dish_matrix), 2)
        self.assertEqual(
            {(row.member, row.verdict) for row in result.dish_matrix},
            {("爷爷", "需调整"), ("弟弟", "需调整")},
        )
        self.assertFalse(any("模型猜测" in line for line in result.member_adjustments))
        self.assertTrue(any("爷爷：" in line and "切小块" in line for line in result.member_adjustments))
        self.assertTrue(any("弟弟：" in line and "主食减半" in line for line in result.member_adjustments))

    def test_inactive_member_allergen_becomes_separate_replacement(self):
        answer = ChefAnswer(recipes=[_recipe("油焖大虾")])
        result = agent_graph._apply_family_differentiation(
            answer,
            self._family_members(),
        )
        lookup = {
            (row.member, row.verdict)
            for row in result.dish_matrix
        }
        self.assertIn(("爷爷", "不可吃"), lookup)
        self.assertIn(("弟弟", "需调整"), lookup)
        self.assertTrue(
            any(
                line.startswith("爷爷：")
                and "单独替换" in line
                and "避免交叉接触" in line
                for line in result.member_adjustments
            )
        )

    def test_missing_family_clears_unverifiable_model_matrix(self):
        answer = ChefAnswer(
            recipes=[_recipe("清炒时蔬")],
            member_adjustments=["模型自填"],
            dish_matrix=[
                DishMatrixItem(
                    dish="清炒时蔬",
                    member="爷爷",
                    verdict="可吃",
                    reason="模型自填",
                )
            ],
        )
        result = agent_graph._apply_family_differentiation(answer, [])
        self.assertEqual(result.member_adjustments, [])
        self.assertEqual(result.dish_matrix, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
