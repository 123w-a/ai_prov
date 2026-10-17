"""阶段 3 联合决策闭环：共同主菜、成员调整和逐人安全矩阵。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent.graph as agent_graph
import main
from agent.chains import rank_recipes
from agent.schemas import ChefAnswer, DishMatrixItem, Recipe, Seasoning


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
            profile_path = Path(temp_dir) / "agent" / "data" / "profile.json"
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
                "_PROFILE_PATH",
                profile_path,
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

    def test_active_profile_allergens_do_not_union_members(self):
        profile_path = Path("data/.test-profile-active-allergens.json")
        try:
            profile_path.write_text(
                json.dumps(
                    {
                        "active_id": "child",
                        "members": [
                            {"id": "child", "profile": {"allergens": ["花生"]}},
                            {"id": "parent", "profile": {"allergens": ["虾"]}},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(agent_graph, "_PROFILE_PATH", profile_path):
                self.assertEqual(agent_graph._active_profile_allergens(), ["花生"])
        finally:
            profile_path.unlink(missing_ok=True)

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


class FamilyMatrixMergeTest(unittest.TestCase):
    """规则沉默时保留模型算对的分餐结论（盐/酱油改证据式判定后的退化修复）

    回归背景（实测 sessions/user_d2cc49f12c 第 4 轮）：家常红烧肉只写「生抽15ml 老抽5ml」
    这类带用量的调料，证据式判定拿不到硬命中 ⇒ 确定性矩阵整片「可吃 / 空理由」，
    再被 `_apply_family_differentiation` 覆盖回去，把模型算对的
    「爸爸·高血压：需调整·只夹2~3块瘦的」一并抹平成「可按共同做法正常食用」。

    口径：规则有结论时永远规则赢，这里只补规则沉默的格子。
    """

    def _members(self):
        return [
            {"name": "爸爸", "profile": {"conditions": ["高血压"]}},
            {"name": "妈妈", "profile": {"conditions": ["尿酸高"]}},
            {"name": "孩子", "profile": {"allergens": ["花生"]}},
        ]

    def _braised_pork(self):
        return ChefAnswer(
            recipes=[
                Recipe(
                    name="减盐减油版红烧肉",
                    intro="干煸逼油、少盐收口",
                    difficulty=2,
                    nutrition=3,
                    seasonings=[
                        Seasoning(name="生抽", amount="15ml"),
                        Seasoning(name="老抽", amount="5ml"),
                        Seasoning(name="冰糖", amount="8g"),
                        Seasoning(name="料酒", amount="20ml"),
                    ],
                    steps=["焯水后干煸逼油", "小火炖50分钟后撇油"],
                )
            ],
            member_adjustments=[
                "孩子：不撒花生碎、不用花生油",
                "爸爸：只夹2~3块瘦的，涮掉表面酱汁",
            ],
            dish_matrix=[
                DishMatrixItem(
                    dish="减盐减油版红烧肉",
                    member="爸爸",
                    verdict="需调整",
                    reason="只夹2~3块瘦的，涮掉表面酱汁",
                )
            ],
        )

    def test_model_adjustment_survives_whole_family_degeneration(self):
        result = agent_graph._apply_family_differentiation(
            self._braised_pork(), self._members()
        )
        father = next(row for row in result.dish_matrix if row.member == "爸爸")
        self.assertEqual(father.verdict, "需调整")
        self.assertIn("涮掉表面酱汁", father.reason)
        self.assertTrue(
            any("爸爸" in line and "涮掉表面酱汁" in line
                for line in result.member_adjustments)
        )
        # 规则沉默的另外两位仍按原口径收口，不被这条修复带偏
        self.assertTrue(
            all(line.split("：", 1)[0] in {"爸爸", "妈妈", "孩子"}
                for line in result.member_adjustments)
        )

    def test_rule_hit_still_overrides_model_row(self):
        answer = ChefAnswer(
            recipes=[_recipe("腰果鸡丁")],
            dish_matrix=[
                DishMatrixItem(
                    dish="腰果鸡丁", member="爸爸", verdict="可吃", reason="模型说没事",
                )
            ],
        )
        with patch.object(
            agent_graph,
            "build_matrix",
            return_value=[
                {"dish": "腰果鸡丁", "member": "爸爸", "verdict": "需调整",
                 "reason": "高血压忌生抽；可单独做低盐版本"},
            ],
        ):
            result = agent_graph._apply_family_differentiation(
                answer, [{"name": "爸爸", "profile": {"conditions": ["高血压"]}}]
            )
        self.assertEqual(len(result.dish_matrix), 1)
        self.assertEqual(result.dish_matrix[0].verdict, "需调整")
        self.assertIn("低盐", result.dish_matrix[0].reason)

    def test_rule_hit_adjustment_line_is_not_replaced_by_model_line(self):
        answer = ChefAnswer(
            recipes=[_recipe("油焖大虾")],
            member_adjustments=["爷爷：可以少吃一点"],
        )
        with patch.object(
            agent_graph,
            "build_matrix",
            return_value=[
                {"dish": "油焖大虾", "member": "爷爷", "verdict": "不可吃",
                 "reason": "虾过敏；需单独替换并避免交叉接触"},
            ],
        ):
            result = agent_graph._apply_family_differentiation(
                answer, [{"name": "爷爷", "profile": {"allergens": ["虾"]}}]
            )
        self.assertTrue(
            any("单独替换" in line for line in result.member_adjustments)
        )
        self.assertFalse(
            any("可以少吃一点" in line for line in result.member_adjustments)
        )

    def test_empty_model_matrix_keeps_deterministic_output(self):
        answer = ChefAnswer(recipes=[_recipe("清炒时蔬")])
        with patch.object(
            agent_graph,
            "build_matrix",
            return_value=[
                {"dish": "清炒时蔬", "member": "爸爸", "verdict": "可吃", "reason": ""},
            ],
        ):
            result = agent_graph._apply_family_differentiation(
                answer, [{"name": "爸爸", "profile": {"conditions": ["高血压"]}}]
            )
        self.assertEqual(
            [(row.member, row.verdict) for row in result.dish_matrix],
            [("爸爸", "可吃")],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
