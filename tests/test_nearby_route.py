"""附近餐厅与定位解析路由的确定性回归测试。"""

import unittest
from unittest.mock import patch

from api.routes import nearby_route


class NearbyRouteTest(unittest.TestCase):
    def test_nearby_reports_warning_on_mock_fallback(self):
        with patch.object(nearby_route, "_amap_poi_search", return_value=None) as mock_search:
            payload = nearby_route.nearby(query="火锅", city="益阳", district="赫山", budget=50, location="113.0,28.0", page=2)

        self.assertEqual(mock_search.call_args.kwargs.get("page"), 2)
        self.assertEqual(payload["data"]["source"], "mock")
        self.assertEqual(payload["data"]["warning"], "网络异常，展示模拟参考餐厅")
        self.assertGreater(len(payload["data"]["restaurants"]), 0)

    def test_nearby_allows_unlimited_budget(self):
        sample = [
            {
                "name": "老街家常菜",
                "cuisine": "家常菜",
                "avg_price": 58,
                "distance_km": 1.2,
                "address": "示例地址",
                "guardrail": "点蒸煮炖、避开红烧/干锅；叮嘱少油少盐",
            }
        ]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample):
            payload = nearby_route.nearby(query="", city="益阳", district="赫山", budget=0, location="113.0,28.0", page=1)

        self.assertEqual(payload["data"]["source"], "amap")
        self.assertEqual(payload["data"]["restaurants"][0]["name"], "老街家常菜")

    def test_resolve_location_without_key_is_honest(self):
        with patch.object(nearby_route, "AMAP_KEY", ""):
            payload = nearby_route.resolve_location("113.0,28.0")

        self.assertFalse(payload["data"]["resolved"])
        self.assertEqual(payload["data"]["label"], "定位解析失败，请手动选择城市")
        self.assertEqual(payload["data"]["warning"], "定位解析失败，请手动选择城市")


if __name__ == "__main__":
    unittest.main(verbosity=2)
