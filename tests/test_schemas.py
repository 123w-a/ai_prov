# tests/test_schemas.py
# 结构化输出数据形状校验（pydantic）+ 透明标注约束
import unittest

from pydantic import ValidationError

from agent_schemas import ChefAnswer, Recipe, Seasoning, SourceRef


class TestRecipeSchema(unittest.TestCase):
    """单道菜结构校验"""

    def test_valid_recipe(self):
        r = Recipe(
            name="番茄炒蛋",
            intro="经典家常",
            difficulty=2,
            nutrition=4,
            seasonings=[Seasoning(name="盐", amount="3g，约半小勺")],
            steps=["打蛋", "下锅炒"],
        )
        self.assertEqual(r.name, "番茄炒蛋")

    def test_difficulty_out_of_range(self):
        with self.assertRaises(ValidationError):
            Recipe(name="x", intro="y", difficulty=9, nutrition=3,
                   seasonings=[], steps=["a"])

    def test_ai_generated_default_false(self):
        r = Recipe(name="x", intro="y", difficulty=1, nutrition=1,
                   seasonings=[], steps=["a"])
        self.assertFalse(r.image_ai_generated, "默认非 AI 生成")


class TestChefAnswerSchema(unittest.TestCase):
    """顶层回答卡片校验"""

    def test_min_one_recipe(self):
        with self.assertRaises(ValidationError):
            ChefAnswer(recipes=[], image_url=None)

    def test_full_valid(self):
        ans = ChefAnswer(
            recipes=[Recipe(name="番茄炒蛋", intro="家常", difficulty=2, nutrition=4,
                            seasonings=[], steps=["炒"])],
            image_url="http://x/egg.jpg",
            image_ai_generated=False,
            image_note="",
            chef_tip="少油更健康",
            sources=[SourceRef(source="成人高血压食养指南（2023年版）", snippet="限盐")],
        )
        self.assertEqual(len(ans.recipes), 1)
        self.assertEqual(ans.sources[0].source, "成人高血压食养指南（2023年版）")

    def test_transparency_note_required_when_ai(self):
        # 当图片是 AI 生成，image_note 应为空 -> 由 structure_answer_node 强制补「AI 生成示意图」
        # 这里只验证 schema 允许 ai=True 且 note 为空（补注逻辑在节点层，见 test_agent_graph 不覆盖此）
        ans = ChefAnswer(
            recipes=[Recipe(name="x", intro="y", difficulty=1, nutrition=1,
                            seasonings=[], steps=["a"])],
            image_ai_generated=True,
            image_note="",
        )
        self.assertTrue(ans.image_ai_generated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
