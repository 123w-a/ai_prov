"""安全语义与并发护栏回归测试。

覆盖 7 类曾经真实出问题、或明确要求固化的行为：
1. 可选过敏原（芝麻/软体动物/亚硫酸盐）必须真的参与矩阵判定；
2. 非激活成员「不可吃」必须出现在右侧护栏，且聚合状态不能显示成「无约束」；
3. 并发更新家庭档案不能丢成员；
4. 同一 session_id 不能同时跑两轮，全局 Agent 并发要有上限；
5. 超大上传和「假图片」必须被拒；
6. 会话 JSON 损坏要回退 .bak，不能静默隐藏；
7. 「上门维修」这类语境不能再被误判成上门私厨。
"""

import asyncio
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.datastructures import Headers, UploadFile

import agent_graph
import sessions_store
import upload_guard
from api.routes import chat_route, preferences_route
from constraint_rules import audit_constraint, build_matrix
from fastapi import HTTPException


def _upload(data: bytes, filename: str = "a.jpg", content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


_PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class OptionalAllergenTest(unittest.TestCase):
    """第 3 项：use_optional 必须穿到 audit_constraint / build_matrix。"""

    def test_audit_constraint_flags_optional_allergens(self):
        for value, dish in (
            ("芝麻", "凉拌菠菜 芝麻酱"),
            ("亚硫酸盐", "葡萄酒 亚硫酸盐"),
            ("软体动物", "辣炒蛤蜊"),
        ):
            with self.subTest(allergen=value):
                violations, _ = audit_constraint(
                    dish, [{"member": "我", "dimension": "allergen", "value": value}]
                )
                self.assertTrue(violations, f"{value} 应当被硬拦截")
                self.assertEqual(violations[0]["dimension"], "allergen")

    def test_matrix_marks_optional_allergen_as_cannot_eat(self):
        rows = build_matrix(
            [{"name": "凉拌菠菜", "ingredients": "菠菜 芝麻酱"}],
            [{"name": "我", "profile": {"allergens": ["芝麻"]}}],
        )
        self.assertEqual(rows[0]["verdict"], "不可吃")
        self.assertIn("交叉接触", rows[0]["reason"])

    def test_unresolved_allergen_becomes_pending_not_eatable(self):
        """芒果/酒精这类没有规则词表的过敏原：不能判「可吃」，要判「待确认」。"""
        rows = build_matrix(
            [{"name": "芒果班戟", "ingredients": "芒果"}],
            [{"name": "奶奶", "profile": {"allergens": ["芒果"]}}],
        )
        self.assertEqual(rows[0]["verdict"], "待确认")
        self.assertIn("需人工确认", rows[0]["reason"])

    def test_unresolved_allergen_does_not_downgrade_unrelated_dish(self):
        """未归一过敏原是「覆盖缺口」，不该把无关菜品也标成需调整。"""
        rows = build_matrix(
            [{"name": "白灼基围虾", "ingredients": "基围虾"}],
            [{"name": "奶奶", "profile": {"allergens": ["芒果"]}}],
        )
        self.assertEqual(rows[0]["verdict"], "可吃")

    def test_unresolved_allergen_is_visible_in_adjustments(self):
        violations, adjustments = audit_constraint(
            "芒果班戟", [{"member": "奶奶", "dimension": "allergen", "value": "芒果"}]
        )
        self.assertEqual(violations, [])
        self.assertTrue(
            any("需人工确认" in item.get("advice", "") for item in adjustments)
        )


class MemberConflictGuardrailTest(unittest.TestCase):
    """第 2 项：矩阵说「不可吃」，右栏就必须显示成员冲突。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        # 把 agent_graph 的 data/profile.json 指到空目录，避免读到真实档案影响断言
        self._patch_file = patch.object(
            agent_graph, "__file__", str(Path(self._temp.name) / "agent_graph.py")
        )
        self._patch_file.start()

    def tearDown(self):
        self._patch_file.stop()
        self._temp.cleanup()

    def test_no_labels_and_no_matrix_yields_empty(self):
        self.assertEqual(agent_graph._build_guardrails("今晚吃什么", "ok", []), [])

    def test_matrix_conflict_reaches_guardrails_with_actionable_copy(self):
        matrix = [
            {
                "dish": "白灼基围虾",
                "member": "爷爷",
                "verdict": "不可吃",
                "reason": "过敏原:甲壳纲类动物及其制品忌基围虾；需单独替换并避免交叉接触",
            }
        ]
        items = agent_graph._build_guardrails("今晚吃什么", "ok", [], dish_matrix=matrix)
        self.assertTrue(items, "矩阵有冲突时右栏不能是空")
        conflicts = [item for item in items if item.status == "member_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("爷爷", conflicts[0].condition)
        self.assertIn("单独替换", conflicts[0].rule)
        self.assertIn("交叉接触", conflicts[0].rule)
        # 不能回落成「已调整/已通过」这类弱表达
        self.assertNotIn("已调整", conflicts[0].reason)

    def test_pending_matrix_row_surfaces_as_warn(self):
        matrix = [
            {"dish": "芒果班戟", "member": "奶奶", "verdict": "待确认", "reason": "芒果：需人工确认"}
        ]
        items = agent_graph._build_guardrails("今晚吃什么", "ok", [], dish_matrix=matrix)
        warns = [item for item in items if item.status == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("奶奶", warns[0].condition)

    def test_broken_profile_reports_degraded_guardrails(self):
        """档案损坏时不能装作「没有约束」，必须如实说护栏降级了。"""
        data_dir = Path(self._temp.name) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "profile.json").write_text("{不是合法 JSON", encoding="utf-8")
        items = agent_graph._build_guardrails("今晚吃什么", "ok", [])
        self.assertTrue(any(item.condition == "健康档案不可用" for item in items))
        self.assertTrue(any("降级" in item.rule for item in items))


class ProfileWriteLockTest(unittest.TestCase):
    """第 4 项：并发「读 → 改 → 写」不能丢成员。"""

    def test_concurrent_member_add_keeps_every_member(self):
        """并发 add_member 不能丢成员。

        空目录下 _read_family() 返回 None，add_member 会先 _migrate({})
        造出 1 个基线成员「我的档案」，再加人。总上限 8 人，因此并发数取 7，
        最终 1 + 7 = 8，既能全成功又刚好顶到上限——这样断言才不会因为
        撞上限而误报（撞上限是产品预期，不是并发缺陷）。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            with patch.object(preferences_route, "_PROFILE_PATH", path):
                workers = 7
                baseline = 1   # _migrate({}) 自动建的「我的档案」
                barrier = threading.Barrier(workers)
                errors = []

                def worker(index):
                    try:
                        barrier.wait(timeout=10)
                        preferences_route.add_member(
                            preferences_route.MemberPayload(
                                name=f"成员{index}",
                                profile=preferences_route.HealthProfilePayload(),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - 记录后统一断言
                        errors.append(exc)

                threads = [
                    threading.Thread(target=worker, args=(i,)) for i in range(workers)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=20)

                self.assertEqual(errors, [])
                family = preferences_route._read_family()
                self.assertEqual(len(family["members"]), workers + baseline)
                names = {m["name"] for m in family["members"]}
                self.assertEqual(
                    names, {"我的档案"} | {f"成员{i}" for i in range(workers)}
                )

    def test_broken_profile_raises_instead_of_silently_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            path.write_text("{坏掉的档案", encoding="utf-8")
            with patch.object(preferences_route, "_PROFILE_PATH", path):
                with self.assertRaises(HTTPException) as ctx:
                    preferences_route._read_family()
                self.assertEqual(ctx.exception.status_code, 500)
                self.assertIn("损坏", ctx.exception.detail)


class TurnConcurrencyTest(unittest.TestCase):
    """第 5 项：同会话串行 + 全局 Agent 并发上限。"""

    def test_same_session_second_turn_is_rejected(self):
        session = "regression-same-session"
        self.assertEqual(chat_route._try_begin_turn(session), "")
        try:
            self.assertTrue(chat_route._try_begin_turn(session), "同会话第二轮必须被拒")
        finally:
            chat_route._end_turn(session)
        self.assertEqual(
            chat_route._try_begin_turn(session), "", "占用已释放，应可再次进入"
        )
        chat_route._end_turn(session)

    def test_different_sessions_do_not_block_each_other(self):
        self.assertEqual(chat_route._try_begin_turn("session-a"), "")
        self.assertEqual(chat_route._try_begin_turn("session-b"), "")
        chat_route._end_turn("session-a")
        chat_route._end_turn("session-b")

    def test_agent_slot_has_an_upper_bound(self):
        acquired = 0
        try:
            while chat_route._acquire_agent_slot():
                acquired += 1
                if acquired > chat_route._MAX_CONCURRENT_AGENT_TURNS + 2:
                    break
            self.assertEqual(acquired, chat_route._MAX_CONCURRENT_AGENT_TURNS)
            self.assertFalse(chat_route._acquire_agent_slot())
        finally:
            for _ in range(acquired):
                chat_route._release_agent_slot()


class UploadGuardTest(unittest.TestCase):
    """第 6 项：体积上限 + 真实格式校验，且不信客户端声明。"""

    def test_oversized_upload_is_rejected_while_reading(self):
        upload = _upload(_PNG_HEAD + b"\x00" * 4096)
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(upload_guard.validate_image_upload(upload, limit=1024))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_declared_mime_is_not_trusted(self):
        # 声明 image/jpeg，实际是纯文本 → 必须按真实内容拒绝
        upload = _upload(b"this is definitely not an image", content_type="image/jpeg")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(upload_guard.validate_image_upload(upload))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不是有效图片", ctx.exception.detail)

    def test_disallowed_declared_type_is_rejected(self):
        upload = _upload(_PNG_HEAD, filename="a.txt", content_type="text/plain")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(upload_guard.validate_image_upload(upload))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_valid_image_returns_sniffed_mime(self):
        upload = _upload(_PNG_HEAD, filename="a.jpg", content_type="image/jpg")
        data, mime = asyncio.run(upload_guard.validate_image_upload(upload))
        self.assertEqual(mime, "image/png")   # 以文件头为准，不用声明值
        self.assertEqual(data, _PNG_HEAD)

    def test_sniff_image_type_recognises_common_containers(self):
        self.assertEqual(upload_guard.sniff_image_type(_JPEG_HEAD), "image/jpeg")
        self.assertEqual(upload_guard.sniff_image_type(_PNG_HEAD), "image/png")
        self.assertEqual(upload_guard.sniff_image_type(b"RIFF____WEBPVP8 "), "image/webp")
        self.assertEqual(upload_guard.sniff_image_type(b"plain text"), "")

    def test_route_modules_import_the_guard_they_call(self):
        """调用了 validate_image_upload 的路由模块必须真的导入它。

        真实踩过：session_route.py 调了 validate_image_upload 但漏了 import，
        平时不报错（那条分支没人走），一旦「带图补录消息」就 NameError。
        这类「用了没导入」用普通单测抓不到，只能按模块属性核对。
        """
        import importlib

        for name in (
            "api.routes.chat_route",
            "api.routes.fridge_route",
            "api.routes.session_route",
        ):
            with self.subTest(module=name):
                module = importlib.import_module(name)
                self.assertTrue(
                    hasattr(module, "validate_image_upload"),
                    f"{name} 调用了 validate_image_upload 但没导入",
                )


class SessionRecoveryTest(unittest.TestCase):
    """第 7 项：损坏会话回退 .bak，不静默隐藏。"""

    def test_corrupt_session_falls_back_to_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_dir = Path(temp_dir)
            with patch.object(sessions_store, "SESSIONS_DIR", sessions_dir):
                good = {
                    "session_id": "s1",
                    "title": "红烧排骨",
                    "created_at": "10:00",
                    "messages": [],
                }
                (sessions_dir / "s1.json").write_text("{半截 JSON", encoding="utf-8")
                (sessions_dir / "s1.json.bak").write_text(
                    json.dumps(good, ensure_ascii=False), encoding="utf-8"
                )

                listed = sessions_store.list_sessions()
                self.assertEqual(len(listed), 1, "有备份的会话不能被隐藏")
                self.assertEqual(listed[0]["title"], "红烧排骨")
                self.assertEqual(sessions_store._read_session("s1")["session_id"], "s1")

    def test_unrecoverable_session_is_skipped_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_dir = Path(temp_dir)
            with patch.object(sessions_store, "SESSIONS_DIR", sessions_dir):
                (sessions_dir / "bad.json").write_text("{坏的", encoding="utf-8")
                (sessions_dir / "bad.json.bak").write_text("{也是坏的", encoding="utf-8")
                self.assertEqual(sessions_store.list_sessions(), [])
                self.assertIsNone(sessions_store._read_session("bad"))


class HomeServiceIntentTest(unittest.TestCase):
    """第 8 项：收窄「上门」裸匹配造成的误判。"""

    def test_repair_and_errand_context_is_not_home_service(self):
        for text in (
            "上周上门维修的师傅说我家冰箱该换了",
            "帮我叫个上门取件的",
            "上门安装热水器要多少钱",
            "明天有人上门做保洁",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(chat_route._classify_turn_intent(text), "home_service")

    def test_real_private_chef_request_still_detected(self):
        for text in (
            "我想请个私厨上门做菜",
            "有没有上门做饭的服务",
            "帮我预约厨师来家里做一桌",
            "想找厨师到家做顿饭",
        ):
            with self.subTest(text=text):
                self.assertEqual(chat_route._classify_turn_intent(text), "home_service")

    def test_plain_private_chef_dish_is_no_longer_redirected(self):
        """「想吃私厨菜」是口味需求，不该被罐头文案接管。"""
        self.assertNotEqual(
            chat_route._classify_turn_intent("我想吃私厨菜"), "home_service"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
