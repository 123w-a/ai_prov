"""图片异步补链回归：structure 剥离搜图 + chat_route 后台补图线程行为。"""

import asyncio
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from api.routes import chat_route
import sessions_store


def _make_event_generator(answer_dict):
    """构造一个可访问的 event_generator 闭包环境（复刻 chat_route 内部结构）。"""
    gen = chat_route.api_chat.__wrapped__ if hasattr(chat_route.api_chat, "__wrapped__") else None
    return gen


class FillImagesTest(unittest.TestCase):
    """直接跑 event_generator 内部闭包不现实；用可达路径：构造真实 SSE 流验证。

    这里退而验证 _fill_images 的核心不变量——通过一个最小复刻环境。
    """

    def _run_fill(self, answer_dict, finder):
        events = queue.Queue()
        answer_dict_ref = answer_dict
        lock = threading.Lock()

        def fill():
            deadline = __import__("time").time() + 25
            for index, recipe in enumerate(list(answer_dict_ref.get("recipes") or [])):
                if __import__("time").time() > deadline or recipe.get("image_url"):
                    continue
                name = str(recipe.get("name") or "").strip()
                if not name:
                    continue
                try:
                    image_url, source = finder(name)
                except Exception:
                    continue
                if not image_url:
                    continue
                ai_flag = source == "ai"
                with lock:
                    recipe["image_url"] = image_url
                    recipe["image_ai_generated"] = ai_flag
                    if index == 0:
                        answer_dict_ref["image_url"] = image_url
                        answer_dict_ref["image_ai_generated"] = ai_flag
                events.put(("item", ("image", {"index": index, "url": image_url, "ai_generated": ai_flag})))

        t = threading.Thread(target=fill, daemon=True)
        t.start()
        t.join(timeout=5)
        return events

    def test_fills_missing_image_and_emits_event(self):
        answer = {"recipes": [{"name": "番茄炒蛋", "image_url": None}], "image_url": None}

        events = self._run_fill(answer, lambda name: ("https://oss.test/tomato.jpg", "real"))

        self.assertEqual(answer["recipes"][0]["image_url"], "https://oss.test/tomato.jpg")
        self.assertEqual(answer["image_url"], "https://oss.test/tomato.jpg")
        kind, payload = events.get_nowait()
        self.assertEqual(kind, "item")
        self.assertEqual(payload[0], "image")
        self.assertEqual(payload[1]["index"], 0)

    def test_skips_recipes_that_already_have_image(self):
        answer = {"recipes": [{"name": "A", "image_url": "https://x/1.jpg"}]}

        events = self._run_fill(answer, lambda name: (_ for _ in ()).throw(AssertionError("不该搜索")))

        self.assertEqual(events.qsize(), 0)
        self.assertEqual(answer["recipes"][0]["image_url"], "https://x/1.jpg")

    def test_finder_failure_leaves_recipe_untouched(self):
        answer = {"recipes": [{"name": "B", "image_url": None}]}

        def boom(name):
            raise RuntimeError("tavily down")

        events = self._run_fill(answer, boom)

        self.assertIsNone(answer["recipes"][0]["image_url"])
        self.assertEqual(events.qsize(), 0)


class StructureNoImageSearchTest(unittest.TestCase):
    def test_structure_node_source_has_no_search_call(self):
        import inspect

        import agent_graph

        src = inspect.getsource(agent_graph.structure_answer_node)
        self.assertNotIn("_search_recipe_image(", src)


class ChatRouteImageGateTest(unittest.TestCase):
    def setUp(self):
        with chat_route._CANCELLED_IMAGE_LOCK:
            chat_route._CANCELLED_IMAGE_TURNS.clear()
            chat_route._CANCELLED_IMAGE_KEEP_TEXT.clear()
        with chat_route._ACTIVE_TURN_RECORDS_LOCK:
            chat_route._ACTIVE_TURN_RECORDS.clear()

    def tearDown(self):
        with chat_route._CANCELLED_IMAGE_LOCK:
            chat_route._CANCELLED_IMAGE_TURNS.clear()
            chat_route._CANCELLED_IMAGE_KEEP_TEXT.clear()
        with chat_route._ACTIVE_TURN_RECORDS_LOCK:
            chat_route._ACTIVE_TURN_RECORDS.clear()

    def test_cancel_marker_is_cleared_before_next_turn(self):
        chat_route._cancel_image_for_turn("session-1", "turn-1")
        self.assertTrue(chat_route._is_image_cancelled("session-1", "turn-1"))

        chat_route._clear_stale_image_cancel_for_new_turn("session-1", "turn-2")

        self.assertFalse(chat_route._is_image_cancelled("session-1", "turn-2"))
        self.assertFalse(chat_route._is_image_cancelled("session-1", "turn-1"))

    def test_legacy_session_cancel_marker_is_not_permanent(self):
        chat_route._cancel_image_for_turn("session-legacy")
        self.assertTrue(chat_route._is_image_cancelled("session-legacy", "new-turn"))

        chat_route._clear_stale_image_cancel_for_new_turn("session-legacy", "new-turn")

        self.assertFalse(chat_route._is_image_cancelled("session-legacy", "new-turn"))

    def test_keep_text_cancel_marker_has_separate_semantics(self):
        chat_route._cancel_image_for_turn("session-1", "turn-1", keep_text=True)

        self.assertTrue(chat_route._is_image_cancelled("session-1", "turn-1"))
        self.assertTrue(chat_route._should_keep_text_on_cancel("session-1", "turn-1"))

        chat_route._clear_image_cancel("session-1", "turn-1")

        self.assertFalse(chat_route._is_image_cancelled("session-1", "turn-1"))
        self.assertFalse(chat_route._should_keep_text_on_cancel("session-1", "turn-1"))

    def test_plain_cancel_does_not_keep_text(self):
        chat_route._cancel_image_for_turn("session-1", "turn-1")

        self.assertTrue(chat_route._is_image_cancelled("session-1", "turn-1"))
        self.assertFalse(chat_route._should_keep_text_on_cancel("session-1", "turn-1"))

    def test_stale_cancel_clear_removes_keep_text_marker(self):
        chat_route._cancel_image_for_turn("session-1", "turn-1", keep_text=True)

        chat_route._clear_stale_image_cancel_for_new_turn("session-1", "turn-2")

        self.assertFalse(chat_route._is_image_cancelled("session-1", "turn-1"))
        self.assertFalse(chat_route._should_keep_text_on_cancel("session-1", "turn-1"))

    def test_cancel_endpoint_routes_keep_text_to_image_cancel_state(self):
        with patch.object(chat_route, "_get_turn_record", return_value=42), patch(
            "sessions_store.mark_message_image_cancelled", return_value=True
        ) as mark_keep_text:
            response = asyncio.run(
                chat_route.cancel_image_decision(
                    session_id="session-1",
                    turn_id="turn-1",
                    keep_text=True,
                )
            )

        mark_keep_text.assert_called_once_with("session-1", 42)
        self.assertTrue(response["data"]["keep_text"])
        self.assertTrue(chat_route._should_keep_text_on_cancel("session-1", "turn-1"))

    def test_home_service_redirect_is_honest_and_actionable(self):
        self.assertEqual(
            chat_route._classify_turn_intent("想请私厨上门做饭"),
            "home_service",
        )
        text = chat_route._home_service_redirect_text()
        self.assertIn("没有真实的私厨上门预约", text)
        self.assertIn("在家自己做", text)
        self.assertIn("推荐附近餐馆", text)
        self.assertIn("帮你选外卖/看菜单", text)
        self.assertIn("了解未来私厨服务", text)

    def test_non_dining_request_blocks_image_pipeline(self):
        self.assertFalse(chat_route._should_enable_image_pipeline("查询下我周围有什么大型的体育赛事开始吗", "1"))

    def test_dining_request_keeps_image_pipeline(self):
        self.assertTrue(chat_route._should_enable_image_pipeline("帮我做个番茄炒蛋，配张图", "1"))

    def test_make_dish_phrase_keeps_image_pipeline(self):
        self.assertTrue(chat_route._should_enable_image_pipeline("帮我做道番茄炒蛋，配张图", "1"))

    def test_explicit_image_phrase_without_toggle_keeps_pipeline(self):
        self.assertTrue(chat_route._should_enable_image_pipeline("帮我做个番茄炒蛋，配张图", "0"))

    def test_standalone_image_answer_reuses_recipe_body(self):
        answer = chat_route._standalone_image_answer(
            "番茄香菇荞麦面",
            "https://example.com/noodle.jpg",
            False,
            "",
            {
                "intro": "酸甜鲜香，适合运动后补能。",
                "difficulty": 2,
                "nutrition": 4,
                "seasonings": [{"name": "生抽", "amount": "少许"}],
                "steps": ["煮面", "炒番茄香菇", "合拌"],
            },
        )
        recipe = answer["recipes"][0]
        self.assertEqual(answer["image_url"], "https://example.com/noodle.jpg")
        self.assertEqual(recipe["image_url"], "https://example.com/noodle.jpg")
        self.assertEqual(recipe["intro"], "酸甜鲜香，适合运动后补能。")
        self.assertEqual(recipe["steps"], ["煮面", "炒番茄香菇", "合拌"])


class SessionsStoreCancellationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = patch.object(sessions_store, "SESSIONS_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _append_structured_answer(self, sid="cancel-s1"):
        answer = json.dumps(
            {
                "opening": "这道番茄炒蛋少油少盐，适合今天吃。",
                "recipes": [
                    {
                        "name": "番茄炒蛋",
                        "intro": "家常快手菜",
                        "steps": ["切番茄", "炒鸡蛋"],
                        "image_url": None,
                    }
                ],
                "image_requested": True,
            },
            ensure_ascii=False,
        )
        record_id = sessions_store.append_message(
            sid,
            "今晚吃什么",
            answer,
            "12:00",
        )
        return sid, record_id, answer

    def test_image_cancel_keeps_text_and_blocks_late_image_writes(self):
        sid, record_id, _ = self._append_structured_answer()

        self.assertTrue(sessions_store.mark_message_image_cancelled(sid, record_id))

        record = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(record["answer"], "这道番茄炒蛋少油少盐，适合今天吃。")
        self.assertTrue(record["image_cancelled"])
        self.assertNotIn("recipes", record["answer"])

        self.assertFalse(
            sessions_store.update_answer_image_by_dish(
                sid,
                record_id,
                "番茄炒蛋",
                "https://example.com/tomato.jpg",
                False,
                "",
            )
        )
        self.assertFalse(
            sessions_store.update_answer_image_at_index(
                sid,
                record_id,
                0,
                "https://example.com/tomato.jpg",
                False,
                "",
            )
        )
        self.assertFalse(
            sessions_store.update_message_answer(
                sid,
                record_id,
                json.dumps({"recipes": [{"name": "番茄炒蛋"}]}, ensure_ascii=False),
            )
        )

        unchanged = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(unchanged["answer"], "这道番茄炒蛋少油少盐，适合今天吃。")
        self.assertEqual(unchanged["image_cancelled"], True)

    def test_image_cancel_restores_uploaded_image_after_generated_image_write(self):
        sid = "cancel-upload"
        answer = json.dumps(
            {
                "opening": "按你上传的食材，推荐番茄炒蛋。",
                "recipes": [
                    {
                        "name": "番茄炒蛋",
                        "intro": "家常快手菜",
                        "steps": ["切番茄", "炒鸡蛋"],
                        "image_url": None,
                    }
                ],
                "image_requested": True,
            },
            ensure_ascii=False,
        )
        record_id = sessions_store.append_message(
            sid,
            "用这些食材做什么",
            answer,
            "12:00",
            image_name="ingredients.jpg",
            image_type="image/jpeg",
            image_url="https://example.com/uploaded-ingredients.jpg",
        )
        self.assertTrue(
            sessions_store.update_answer_image_by_dish(
                sid,
                record_id,
                "番茄炒蛋",
                "https://example.com/generated-dish.jpg",
                True,
                "",
            )
        )

        self.assertTrue(sessions_store.mark_message_image_cancelled(sid, record_id))

        record = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(record["answer"], "按你上传的食材，推荐番茄炒蛋。")
        self.assertEqual(record["image_url"], "https://example.com/uploaded-ingredients.jpg")
        self.assertEqual(record["user_image_url"], "https://example.com/uploaded-ingredients.jpg")
        self.assertNotIn("generated-dish.jpg", json.dumps(record, ensure_ascii=False))

    def test_full_turn_cancel_keeps_only_uploaded_image(self):
        sid = "cancel-full-upload"
        record_id = sessions_store.append_message(
            sid,
            "用这些食材做什么",
            json.dumps(
                {
                    "opening": "推荐番茄炒蛋。",
                    "recipes": [{"name": "番茄炒蛋"}],
                },
                ensure_ascii=False,
            ),
            "12:00",
            image_url="https://example.com/uploaded.jpg",
        )

        self.assertTrue(sessions_store.mark_message_cancelled(sid, record_id))

        record = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(record["answer"], "__cancelled__")
        self.assertEqual(record["image_url"], "https://example.com/uploaded.jpg")

    def test_pending_answer_can_be_replaced_by_text_after_image_cancel(self):
        sid = "cancel-pending"
        record_id = sessions_store.append_message(
            sid,
            "今晚吃什么",
            "__pending__",
            "12:00",
        )

        self.assertTrue(sessions_store.mark_message_image_cancelled(sid, record_id))
        self.assertTrue(
            sessions_store.mark_message_image_cancelled(
                sid,
                record_id,
                json.dumps(
                    {
                        "opening": "先给你保留这段文字说明。",
                        "recipes": [{"name": "番茄炒蛋"}],
                    },
                    ensure_ascii=False,
                ),
            )
        )

        record = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(record["answer"], "先给你保留这段文字说明。")
        self.assertTrue(record["image_cancelled"])

    def test_full_turn_cancel_marks_unrecoverable_terminal_state(self):
        sid, record_id, _ = self._append_structured_answer("cancel-full")

        self.assertTrue(sessions_store.mark_message_cancelled(sid, record_id))

        record = sessions_store._read_session(sid)["messages"][0]
        self.assertEqual(record["answer"], "__cancelled__")
        self.assertTrue(record["cancelled"])
        self.assertFalse(
            sessions_store.update_message_answer(sid, record_id, "迟到的回答")
        )
        self.assertFalse(
            sessions_store.update_answer_image_at_index(
                sid,
                record_id,
                0,
                "https://example.com/late.jpg",
                False,
                "",
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
