"""真实过敏原表达与“只提示不硬拦”边界的回归测试。"""

import unittest

import allergen_rules
from experiments.ab_allergen_guardrail import _run_case
from experiments.allergen_cases import REAL_ALLERGEN_CASES


class RealAllergenCasesTest(unittest.TestCase):
    def test_real_case_evaluation_set_passes(self):
        failures = []
        for case in REAL_ALLERGEN_CASES:
            result = _run_case(case)
            if not result["pass"]:
                failures.append(
                    f"{case['id']}: hard={result['guard_on_hits']} "
                    f"notice={result['notice_hits']} expected={result['expected']}"
                )
        self.assertEqual(failures, [])

    def test_explicit_optional_allergen_is_enforced_when_requested(self):
        hard = allergen_rules.audit_allergens(
            "麻酱拌面",
            ["芝麻过敏"],
            use_optional=True,
        )
        self.assertTrue(hard)
        self.assertEqual(hard[0]["keyword"], "麻酱")

    def test_optional_allergen_is_not_enabled_for_unrelated_profiles(self):
        self.assertEqual(
            allergen_rules.audit_allergens("麻酱拌面", ["花生过敏"]),
            [],
        )

    def test_common_upper_level_terms_expand(self):
        self.assertEqual(
            allergen_rules.normalize_allergens("海鲜过敏"),
            ["crustacean", "fish"],
        )
        self.assertEqual(
            allergen_rules.normalize_allergens("芝麻过敏"),
            ["sesame"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
