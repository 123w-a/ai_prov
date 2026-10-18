"""附近餐厅与定位解析路由的确定性回归测试。"""

import json
import unittest
from unittest.mock import patch

import requests
from fastapi import HTTPException

from api.routes import nearby_route


class _FakeResponse:
    """高德响应的最小替身：只用到 text / status_code / json()，与 requests.Response 同形。"""

    def __init__(self, payload, status_code=200, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


class _BadJsonResponse:
    """HTTP 通了但响应体不是 JSON（例如网关 502 页面）。"""

    status_code = 502
    text = "<html><body>502 Bad Gateway</body></html>"

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


# 真实高德返回：本机这个 Key 打 Web 服务接口就是这个结果（2026-10-12 直连实测）
AMAP_REJECTED = {"status": "0", "info": "USERKEY_PLAT_NOMATCH", "infocode": "10009", "count": None}

# 形状照官方《地理/逆地理编码》文档的 addressComponent **手工构造**，不是真实响应
# （真实响应被 Key 平台权限挡在门外，见 AMAP_REJECTED）。
AMAP_OK = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "1",
    "regeocode": {
        "formatted_address": "北京市东城区东华门街道天安门",
        "addressComponent": {
            "country": "中国",
            "province": "北京市",
            "city": "北京市",
            "citycode": "010",
            "district": "东城区",
            "adcode": "110101",
            "township": "东华门街道",
            "towncode": "110101001000",
            "neighborhood": {"name": [], "type": []},
            "building": {"name": [], "type": []},
            "streetNumber": {
                "street": "东长安街",
                "number": "1号",
                "location": "116.397442,39.908672",
                "direction": "东",
                "distance": "0.0000",
            },
            "businessAreas": [{"location": "116.400000,39.900000", "name": "东华门", "id": "110101"}],
        },
    },
}


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

    def test_nearby_filters_optional_allergen_and_reports_it(self):
        sample = [
            {
                "name": "麻酱拌面馆",
                "cuisine": "面食",
                "avg_price": 28,
                "distance_km": 0.8,
                "address": "示例路 1 号",
                "guardrail": "少油少盐",
            },
            {
                "name": "清蒸鸡腿饭",
                "cuisine": "家常菜",
                "avg_price": 32,
                "distance_km": 1.1,
                "address": "示例路 2 号",
                "guardrail": "点蒸煮炖",
            },
        ]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample), \
             patch.object(nearby_route._legacy, "_tool_allergens", return_value=["芝麻"]):
            payload = nearby_route.nearby(city="益阳", district="赫山", budget=50, page=1)

        self.assertEqual(
            [item["name"] for item in payload["data"]["restaurants"]],
            ["清蒸鸡腿饭"],
        )
        self.assertEqual(payload["data"]["allergen_filter"]["removed"], 1)
        self.assertIn("芝麻及其制品", payload["data"]["allergen_filter"]["message"])

    def test_nearby_map_uses_only_real_coordinates_and_bounds_center(self):
        sample = [
            {
                "name": "老街家常菜",
                "cuisine": "家常菜",
                "avg_price": 58,
                "distance_km": 0.8,
                "address": "示例路 1 号",
                "guardrail": "点蒸煮炖",
                "lng": 112.4,
                "lat": 28.5,
            },
            {
                "name": "清蒸鸡腿饭",
                "cuisine": "快餐",
                "avg_price": 32,
                "distance_km": 1.1,
                "address": "示例路 2 号",
                "guardrail": "避开油炸窗口",
                "lng": 112.6,
                "lat": 28.6,
            },
            {
                "name": "坐标坏掉的店",
                "cuisine": "快餐",
                "avg_price": 20,
                "distance_km": 2.0,
                "address": "示例路 3 号",
                "guardrail": "少油少盐",
                "lng": 999,
                "lat": 28.6,
            },
        ]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample):
            payload = nearby_route.nearby_map(city="益阳", district="赫山", budget=0, page=1)

        data = payload["data"]
        self.assertEqual(data["source"], "amap")
        self.assertEqual(data["coordinate_system"], "gcj02")
        self.assertEqual([item["name"] for item in data["markers"]], ["老街家常菜", "清蒸鸡腿饭"])
        self.assertEqual(data["center"], {"lng": 112.5, "lat": 28.55})
        self.assertIsNone(data["origin"])
        self.assertEqual(data["warning"], "")

    def test_nearby_map_uses_given_location_as_origin_and_center(self):
        sample = [
            {
                "name": "老街家常菜",
                "cuisine": "家常菜",
                "avg_price": 58,
                "distance_km": 0.8,
                "address": "示例路 1 号",
                "guardrail": "点蒸煮炖",
                "lng": 112.41,
                "lat": 28.51,
            }
        ]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample) as mock_search:
            payload = nearby_route.nearby_map(
                query="家常菜",
                city="益阳",
                district="赫山",
                budget=50,
                location="[当前位置：112.4,28.5]",
                radius=2000,
                page=2,
            )

        data = payload["data"]
        self.assertEqual(mock_search.call_args.args[4], "112.4,28.5")
        self.assertEqual(mock_search.call_args.kwargs["radius"], 2000)
        self.assertEqual(mock_search.call_args.kwargs["page"], 2)
        self.assertEqual(data["origin"], {"lng": 112.4, "lat": 28.5})
        self.assertEqual(data["center"], {"lng": 112.4, "lat": 28.5})
        self.assertEqual(data["markers"][0]["lng"], 112.41)

    def test_nearby_map_invalid_location_falls_back_without_center(self):
        with patch.object(nearby_route, "_amap_poi_search", return_value=[]) as mock_search:
            payload = nearby_route.nearby_map(location="0,0", budget=50, page=1)

        data = payload["data"]
        self.assertEqual(mock_search.call_args.args[4], "")
        self.assertEqual(data["source"], "mock_empty")
        self.assertIsNone(data["origin"])
        self.assertIsNone(data["center"])
        self.assertEqual(data["markers"], [])
        self.assertIn("定位坐标无法解析", data["warning"])
        self.assertIn("模拟参考餐厅", data["warning"])

    def test_nearby_map_mock_fallback_never_fabricates_markers(self):
        with patch.object(nearby_route, "_amap_poi_search", return_value=None):
            payload = nearby_route.nearby_map(query="火锅", city="益阳", district="赫山", budget=50)

        data = payload["data"]
        self.assertEqual(data["source"], "mock")
        self.assertEqual(data["markers"], [])
        self.assertIsNone(data["center"])
        self.assertIsNone(data["origin"])
        self.assertIn("展示模拟参考餐厅", data["warning"])

    def test_nearby_map_reports_no_key_honestly(self):
        def fake_search(*args, diag=None, **kwargs):
            if diag is not None:
                diag["info"] = "no_key"
            return None

        with patch.object(nearby_route, "AMAP_KEY", ""), \
             patch.object(nearby_route, "_amap_poi_search", side_effect=fake_search):
            payload = nearby_route.nearby_map(budget=50)

        data = payload["data"]
        self.assertFalse(data["amap_configured"])
        self.assertEqual(data["markers"], [])
        self.assertIn("没有配置高德 Key", data["warning"])

    def test_nearby_map_scrubs_key_from_search_failure_warning(self):
        sentinel = "SENTINELKEY0000000000000000000000"

        def fake_search(*args, diag=None, **kwargs):
            if diag is not None:
                diag["exception"] = f"ConnectionError: /v3/place/text?key={sentinel}"
            return None

        with patch.object(nearby_route, "AMAP_KEY", sentinel), \
             patch.object(nearby_route, "_amap_poi_search", side_effect=fake_search):
            payload = nearby_route.nearby_map(budget=50)

        data = payload["data"]
        self.assertTrue(data["amap_configured"])
        self.assertIn("***", data["warning"])
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))

    def test_nearby_map_amap_result_without_coordinates_is_honest(self):
        sample = [
            {
                "name": "没有坐标的店",
                "cuisine": "家常菜",
                "avg_price": 58,
                "distance_km": None,
                "address": "示例地址",
                "guardrail": "点蒸煮炖",
            }
        ]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample):
            payload = nearby_route.nearby_map(city="益阳", budget=50)

        data = payload["data"]
        self.assertEqual(data["source"], "amap")
        self.assertEqual(data["markers"], [])
        self.assertIsNone(data["center"])
        self.assertIn("没有可用的经纬度", data["warning"])

    def test_nearby_map_allergen_filter_is_not_reported_as_missing_coordinates(self):
        sample = [
            {
                "name": "花生酱拌面",
                "cuisine": "面食",
                "avg_price": 28,
                "distance_km": 0.8,
                "address": "示例路 1 号",
                "guardrail": "少油少盐",
                "lng": 112.4,
                "lat": 28.5,
            }
        ]
        hits = [{"condition": "过敏原:花生", "keyword": "花生"}]
        with patch.object(nearby_route, "_amap_poi_search", return_value=sample), \
             patch.object(
                 nearby_route._legacy,
                 "_filter_restaurants_by_allergens",
                 return_value=([], hits),
             ):
            payload = nearby_route.nearby_map(city="益阳", budget=50)

        data = payload["data"]
        self.assertEqual(data["source"], "amap")
        self.assertEqual(data["markers"], [])
        self.assertNotIn("没有可用的经纬度", data["warning"])
        self.assertEqual(data["allergen_filter"]["removed"], 1)
        self.assertIn("花生", data["allergen_filter"]["message"])

    def test_nearby_map_validates_search_params(self):
        with self.assertRaises(HTTPException) as ctx:
            nearby_route.nearby_map(budget=-1)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_resolve_location_without_key_is_honest(self):
        with patch.object(nearby_route, "AMAP_KEY", ""):
            payload = nearby_route.resolve_location("113.0,28.0")

        self.assertFalse(payload["data"]["resolved"])
        self.assertEqual(payload["data"]["location"], "113.0,28.0")
        self.assertEqual(payload["data"]["city"], "")
        self.assertEqual(payload["data"]["district"], "")
        self.assertEqual(payload["data"]["label"], "后端没有配置高德 Key（AMAP_KEY 为空），无法解析位置名")
        self.assertEqual(
            payload["data"]["warning"],
            "后端没有配置高德 Key（AMAP_KEY 为空），无法解析位置名。"
            "请在 .env 里填一个「Web服务」类型的 AMAP_KEY 并重启后端；在那之前仍可按关键词查。",
        )
        self.assertEqual(payload["messages"], payload["data"]["warning"])

    def test_resolve_location_without_coords_is_honest(self):
        payload = nearby_route.resolve_location("   ")

        self.assertFalse(payload["data"]["resolved"])
        self.assertEqual(payload["data"]["location"], "")
        self.assertEqual(payload["data"]["label"], "没有收到坐标（location 为空），无法解析位置名")
        self.assertEqual(
            payload["data"]["warning"],
            "没有收到坐标（location 为空），无法解析位置名。"
            "请点「用我的位置」重试；不想授权定位就按关键词查，附近餐厅照样能出。",
        )
        self.assertEqual(payload["messages"], payload["data"]["warning"])

    def test_resolve_location_platform_mismatch_surfaces_info_and_infocode(self):
        """高德拒答时，info/infocode 必须原样出现在 warning 与 messages 里。"""
        with patch.object(nearby_route.requests, "get", return_value=_FakeResponse(AMAP_REJECTED)) as fake:
            payload = nearby_route.resolve_location("116.3975,39.9087")

        data = payload["data"]
        self.assertFalse(data["resolved"])
        self.assertEqual(data["location"], "116.3975,39.9087")
        self.assertEqual(data["city"], "")
        self.assertEqual(data["district"], "")
        self.assertIn("info=USERKEY_PLAT_NOMATCH", data["warning"])
        self.assertIn("infocode=10009", data["warning"])
        self.assertIn("info=USERKEY_PLAT_NOMATCH", payload["messages"])
        self.assertIn("infocode=10009", payload["messages"])
        # 要说清楚该去哪做什么，而不是把用户丢在「请手动选择城市」
        self.assertIn("Web服务", data["warning"])
        self.assertNotIn("请手动选择城市", data["warning"])
        self.assertEqual(
            data["label"],
            "高德逆地理编码被拒——请求 Key 与绑定的平台不符（例如拿 JS API / Android 的 Key 调 Web 服务接口）",
        )
        # 请求参数没被改坏：坐标按原样发给高德，key 仍在 params 里
        sent = fake.call_args.kwargs["params"]
        self.assertEqual(sent["location"], "116.3975,39.9087")
        self.assertEqual(sent["extensions"], "base")
        self.assertIn("key", sent)

    def test_resolve_location_success_reads_address_component(self):
        """成功分支：桩响应形状照官方文档（标注：这一段是桩，不是真实高德返回）。"""
        with patch.object(nearby_route.requests, "get", return_value=_FakeResponse(AMAP_OK)):
            payload = nearby_route.resolve_location("116.3975,39.9087")

        data = payload["data"]
        self.assertTrue(data["resolved"])
        self.assertEqual(data["city"], "北京市")
        self.assertEqual(data["district"], "东城区")
        self.assertEqual(data["label"], "北京市 · 东城区")
        self.assertEqual(data["warning"], "")
        self.assertEqual(payload["messages"], "定位已就绪")
        self.assertEqual(data["location"], "116.3975,39.9087")

    def test_resolve_location_ok_but_no_address_component_is_not_success(self):
        """高德 status=1 但没有 city/district：不许报成 resolved=true。"""
        payload_ok = {"status": "1", "info": "OK", "infocode": "10000", "regeocode": {"formatted_address": ""}}
        with patch.object(nearby_route.requests, "get", return_value=_FakeResponse(payload_ok)):
            payload = nearby_route.resolve_location("0.0,0.0")

        data = payload["data"]
        self.assertFalse(data["resolved"])
        self.assertEqual(data["city"], "")
        self.assertEqual(data["district"], "")
        self.assertIn("addressComponent", data["warning"])
        self.assertIn("infocode=10000", data["warning"])
        self.assertIn("formatted_address=空", data["warning"])

    def test_resolve_location_network_error_reports_type_and_scrubs_key(self):
        """断网/超时：异常类型与 HTTP 码要带出来，且 Key 绝不能被写进响应。"""
        sentinel = "SENTINELKEY0000000000000000000000"
        boom = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='restapi.amap.com', port=443): Max retries exceeded with url: "
            f"/v3/geocode/regeo?key={sentinel}&location=113.0,28.0"
        )
        with patch.object(nearby_route, "AMAP_KEY", sentinel), \
             patch.object(nearby_route.requests, "get", side_effect=boom):
            payload = nearby_route.resolve_location("113.0,28.0")

        warning = payload["data"]["warning"]
        self.assertFalse(payload["data"]["resolved"])
        self.assertIn("ConnectionError", warning)
        self.assertIn("***", warning)
        self.assertNotIn(sentinel, json.dumps(payload, ensure_ascii=False))

    def test_resolve_location_non_json_body_reports_status_and_snippet(self):
        """HTTP 通了但不是 JSON：带出异常类型、HTTP 码与响应体片段。"""
        with patch.object(nearby_route.requests, "get", return_value=_BadJsonResponse()):
            payload = nearby_route.resolve_location("113.0,28.0")

        warning = payload["data"]["warning"]
        self.assertFalse(payload["data"]["resolved"])
        self.assertIn("ValueError", warning)
        self.assertIn("HTTP 502", warning)
        self.assertIn("502 Bad Gateway", warning)


if __name__ == "__main__":
    unittest.main(verbosity=2)
