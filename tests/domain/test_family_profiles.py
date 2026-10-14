"""P1 家庭多画像回归：v1 迁移、成员 CRUD、激活切换、注入链按激活成员渲染。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import preferences_route as pr
from api.routes import reports_route as rr
from main import load_preferences
from domain.nutrition_rules import audit, conditions_from_profile, detect_conditions


def _client():
    app = FastAPI()
    app.include_router(pr.router)
    return TestClient(app)


class FamilyProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.profile_path = self.data_dir / "profile.json"
        self.prefs_path = self.data_dir / "preferences.txt"
        patcher_p = patch.object(pr, "_PROFILE_PATH", self.profile_path)
        patcher_d = patch.object(pr, "_DATA_DIR", self.data_dir)
        patcher_m = patch("main._PROFILE_PATH", self.profile_path)
        patcher_t = patch("main._PREFS_PATH", str(self.prefs_path))
        # 记账文件也指向临时目录：营养进度会被投影进 /profile，而它读的是 meals.json。
        # 不隔离的话这个测试会读到仓库真实的 data/meals.json（回填后有真值），
        # 断言就会随真实数据漂移——测试要钉代码，不能钉现场数据。
        patcher_meals = patch.object(rr, "_MEALS", self.data_dir / "meals.json")
        fake_tool = type("_FakeGetFile", (), {
            "invoke": staticmethod(lambda args=None, _s=self: (
                _s.prefs_path.read_text(encoding="utf-8")
                if _s.prefs_path.exists() else "文件不存在"
            )),
        })
        patcher_g = patch("main.get_file", fake_tool)
        for p in (patcher_p, patcher_d, patcher_m, patcher_t, patcher_meals, patcher_g):
            p.start()
            self.addCleanup(p.stop)
        self.client = _client()

    def test_missing_profile_returns_default_family(self):
        body = self.client.get("/profile").json()["data"]
        self.assertFalse(body["exists"])
        self.assertEqual(len(body["family"]["members"]), 1)
        self.assertEqual(body["family"]["members"][0]["id"], "me")

    def test_v1_flat_profile_migrates_to_family(self):
        self.profile_path.write_text(
            json.dumps({"conditions": ["高血压"], "allergens": ["花生"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        body = self.client.get("/profile").json()["data"]
        fam = body["family"]
        self.assertEqual(fam["version"], 2)
        self.assertEqual(len(fam["members"]), 1)
        self.assertEqual(fam["members"][0]["profile"]["conditions"], ["高血压"])
        # 落盘已迁移：再次直读文件应为 v2 结构
        on_disk = json.loads(self.profile_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["version"], 2)

    def test_add_update_switch_delete_member(self):
        r1 = self.client.post("/profile/members", json={
            "name": "妈妈",
            "profile": {"conditions": ["高血压"], "allergens": ["虾"]},
        }).json()["data"]["family"]
        self.assertEqual(r1["active_id"], r1["members"][-1]["id"])
        mama_id = r1["members"][-1]["id"]

        r2 = self.client.post("/profile/members", json={
            "name": "爸爸",
            "profile": {"conditions": ["糖尿病"]},
        }).json()["data"]["family"]
        papa_id = r2["members"][-1]["id"]

        # 切换激活到爸爸
        self.client.put("/profile/active", json={"member_id": papa_id})
        fam = self.client.get("/profile").json()["data"]["family"]
        self.assertEqual(fam["active_id"], papa_id)

        # 更新妈妈
        self.client.put(f"/profile/members/{mama_id}", json={
            "name": "母亲大人",
            "profile": {"conditions": ["高血压"], "allergens": ["虾", "蟹"]},
        })
        fam = self.client.get("/profile").json()["data"]["family"]
        mama = next(m for m in fam["members"] if m["id"] == mama_id)
        self.assertEqual(mama["name"], "母亲大人")
        self.assertEqual(mama["profile"]["allergens"], ["虾", "蟹"])

        # 删除激活成员（爸爸）→ 自动切到剩余第一人
        self.client.delete(f"/profile/members/{papa_id}")
        fam = self.client.get("/profile").json()["data"]["family"]
        self.assertEqual(fam["active_id"], fam["members"][0]["id"])

        # 删到最后一人应被拒绝
        for m in fam["members"][1:]:
            self.client.delete(f"/profile/members/{m['id']}")
        last = self.client.get("/profile").json()["data"]["family"]["members"]
        resp = self.client.delete(f"/profile/members/{last[0]['id']}")
        self.assertEqual(resp.status_code, 400)

    def test_load_preferences_renders_active_member(self):
        self.client.post("/profile/members", json={
            "name": "妈妈",
            "profile": {"conditions": ["高血压"], "allergens": ["虾"]},
        })
        rendered = load_preferences()
        self.assertIn("妈妈", rendered)
        self.assertIn("高血压", rendered)
        self.assertIn("虾", rendered)

    def test_load_preferences_falls_back_to_txt(self):
        self.prefs_path.write_text("喜欢清淡。", encoding="utf-8")
        self.assertEqual(load_preferences(), "喜欢清淡。")

    def test_export_and_import_merge(self):
        """共享画像：导出载体 → 导入合并（同名覆盖、新名追加、激活不变）。"""
        self.client.post("/profile/members", json={
            "name": "妈妈",
            "profile": {"conditions": ["高血压"]},
        })
        exported = self.client.get("/profile/export").json()["data"]["export"]
        self.assertEqual(exported["app"], "xiaoshan-profile")
        self.assertTrue(any(m["name"] == "妈妈" for m in exported["members"]))
        before_active = self.client.get("/profile").json()["data"]["family"]["active_id"]

        resp = self.client.post("/profile/import", json={
            "members": [
                {"name": "妈妈", "profile": {"conditions": ["高血压", "糖尿病"]}},
                {"name": "爸爸", "profile": {"conditions": ["痛风"]}},
            ]
        })
        self.assertEqual(resp.status_code, 200)
        fam = resp.json()["data"]["family"]
        names = [m["name"] for m in fam["members"]]
        self.assertIn("爸爸", names)
        self.assertIn("妈妈", names)
        self.assertEqual(len(names), 3)  # 我的档案 + 妈妈覆盖 + 爸爸新增
        mama = next(m for m in fam["members"] if m["name"] == "妈妈")
        self.assertEqual(mama["profile"]["conditions"], ["高血压", "糖尿病"])
        self.assertEqual(fam["active_id"], before_active)

        resp = self.client.get("/profile")
        self.assertEqual(resp.json()["data"]["family"]["active_id"], before_active)

    def test_import_over_limit_rejected(self):
        flood = [{"name": f"成员{i}", "profile": {}} for i in range(8)]
        resp = self.client.post("/profile/import", json={"members": flood})
        self.assertEqual(resp.status_code, 400)

    def test_guardrail_wiring_from_profile(self):
        """P1 护栏联动：档案 conditions（含孕周自由文本）映射规则键并触发硬审计。"""
        self.assertEqual(
            conditions_from_profile({"conditions": ["孕18周", "高尿酸"]}),
            ["孕期", "痛风"],
        )
        self.assertEqual(conditions_from_profile({"conditions": ["喜欢清淡"]}), [])
        merged = detect_conditions("") + conditions_from_profile({"conditions": ["孕18周"]})
        violations = audit("清蒸生鱼片配一杯啤酒", merged)
        self.assertTrue(
            any(v["condition"] == "孕期" for v in violations),
            "档案声明孕18周时，生鱼片+啤酒必须命中孕期硬护栏",
        )

    # ── 2026-10-11 新增两个身体字段：gestational_weeks / body_fat_rate ──────

    def _write_old_v2_profile(self):
        """结构完整的老 v2 档案：basic 里没有两个新字段（模拟旧档案）。"""
        self.profile_path.write_text(json.dumps({
            "version": 2,
            "active_id": "m_old",
            "members": [{
                "id": "m_old", "name": "小美",
                "profile": {
                    "basic": {"height_cm": 165.0, "weight_kg": None, "age": 30, "sex": "female"},
                    "conditions": ["孕18周"], "allergens": [], "restricts": [],
                    "goal": "", "diet_style": "", "dislikes": [], "taste_notes": [],
                },
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def test_new_body_fields_are_null_for_old_profile_and_never_migrated(self):
        """旧档案两个字段返回 null；且**不迁移**——不动磁盘上的档案。"""
        self._write_old_v2_profile()
        basic = self.client.get("/profile").json()["data"]["family"]["members"][0]["profile"]["basic"]
        self.assertIn("gestational_weeks", basic)
        self.assertIn("body_fat_rate", basic)
        self.assertIsNone(basic["gestational_weeks"])
        self.assertIsNone(basic["body_fat_rate"])
        on_disk = json.loads(self.profile_path.read_text(encoding="utf-8"))
        disk_basic = on_disk["members"][0]["profile"]["basic"]
        self.assertNotIn("gestational_weeks", disk_basic)
        self.assertNotIn("body_fat_rate", disk_basic)

    def test_new_body_fields_write_and_read_through_existing_endpoints(self):
        """POST /profile/members 可写、GET /profile 可读（旧端点，未新造接口）。"""
        created = self.client.post("/profile/members", json={
            "name": "孕妈",
            "profile": {
                "conditions": ["孕18周"],
                "basic": {
                    "height_cm": 165.0, "weight_kg": 58.0, "age": 30, "sex": "female",
                    "gestational_weeks": 18, "body_fat_rate": 24.5,
                },
            },
        }).json()["data"]["family"]
        new_id = created["active_id"]
        member = next(m for m in created["members"] if m["id"] == new_id)
        self.assertEqual(member["profile"]["basic"]["gestational_weeks"], 18)
        self.assertEqual(member["profile"]["basic"]["body_fat_rate"], 24.5)

        fam = self.client.get("/profile").json()["data"]["family"]
        member = next(m for m in fam["members"] if m["id"] == new_id)
        self.assertEqual(member["profile"]["basic"]["gestational_weeks"], 18)
        self.assertEqual(member["profile"]["basic"]["body_fat_rate"], 24.5)

        # 另一个成员（默认档）没有登记 → null，不是 0、也不是别人的值
        others = [m for m in fam["members"] if m["id"] != new_id]
        self.assertIsNone(others[0]["profile"]["basic"]["gestational_weeks"])

    def test_put_still_overwrites_whole_member_profile(self):
        """钉住既有 PUT 语义（整成员覆盖）：没带的字段被清空——不是新引入的行为。

        前端因此必须把这两个字段带回 PUT（见 ProfileView 的 onSubmit 合并），
        否则用户编辑一次就把录入的孕周/体脂率静默抹掉。
        """
        created = self.client.post("/profile/members", json={
            "name": "孕妈",
            "profile": {"basic": {"gestational_weeks": 20, "body_fat_rate": 25.0}},
        }).json()["data"]["family"]
        mid = created["active_id"]

        updated = self.client.put(f"/profile/members/{mid}", json={
            "name": "孕妈",
            "profile": {"basic": {"body_fat_rate": 26.0}},
        }).json()["data"]["family"]
        member = next(m for m in updated["members"] if m["id"] == mid)
        self.assertEqual(member["profile"]["basic"]["body_fat_rate"], 26.0)
        self.assertIsNone(member["profile"]["basic"]["gestational_weeks"])

    def test_out_of_range_body_fields_rejected(self):
        resp = self.client.post("/profile/members", json={
            "name": "越界",
            "profile": {"basic": {"gestational_weeks": 99, "body_fat_rate": 200}},
        })
        self.assertEqual(resp.status_code, 422)

    def test_nutrition_progress_shape_and_null_value(self):
        """营养进度：{value,numerator,denominator,period}；本 fixture 一条餐次都没归属到成员。

        分母 0 → value null（不是 0%：那会被读成"一餐都没达标"）。判据（三盏灯全绿）
        与归属过滤的边界在 tests/api/test_nutrition_progress.py。
        period 是真实窗口（与周报同源，见 tests/api/test_reports_route.py 的一致性用例）；
        并且它**不落盘**——算出来的东西写进 profile.json 就会被当成档案里的既有字段。
        """
        fam = self.client.get("/profile").json()["data"]["family"]
        np = fam["members"][0]["profile"]["nutrition_progress"]
        self.assertEqual(set(np), {"value", "numerator", "denominator", "period"})
        self.assertIsNone(np["value"])
        self.assertEqual(np["numerator"], 0)
        self.assertEqual(np["denominator"], 0)
        self.assertRegex(np["period"]["start"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(np["period"]["end"], r"^\d{4}-\d{2}-\d{2}$")

        self.client.post("/profile/members", json={"name": "妈妈", "profile": {}})
        on_disk = self.profile_path.read_text(encoding="utf-8")
        self.assertNotIn("nutrition_progress", on_disk)
        # 也没顺手把两个新字段"迁移"进旧档案以外的任何地方：这里只查进度不进档
        self.assertIn("gestational_weeks", on_disk)  # 写入路径自然带上的空值，语义可空


if __name__ == "__main__":
    unittest.main(verbosity=2)
