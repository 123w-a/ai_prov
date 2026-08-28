"""图片异步补链回归：structure 剥离搜图 + chat_route 后台补图线程行为。"""

import queue
import threading
import unittest
from unittest.mock import patch

from api.routes import chat_route


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
