"""冰箱与采购清单的 FastAPI HTTP 契约回归测试。"""

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main_app import app
from api.routes import fridge_route


class FridgeHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(".fridge-http-test.json")
        self.log_path = Path(__file__).with_name(".pantry-http-test.json")
        self.patch = patch.object(fridge_route, "_FILE", self.path)
        self.patch_log = patch.object(fridge_route, "_PANTRY_LOG", self.log_path)
        self.patch.start()
        self.patch_log.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.patch.stop()
        self.patch_log.stop()
        self.path.unlink(missing_ok=True)
        self.log_path.unlink(missing_ok=True)

    def test_form_set_add_get_preserves_and_deduplicates_inventory(self):
        set_response = self.client.post("/api/fridge/set", data={"items": "鸡蛋,西兰花"})
        self.assertEqual(set_response.status_code, 200)
        self.assertEqual(set_response.json()["items"], ["鸡蛋", "西兰花"])

        add_response = self.client.post("/api/fridge/add", data={"items": "番茄,鸡蛋"})
        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(add_response.json()["items"], ["鸡蛋", "西兰花", "番茄"])

        get_response = self.client.get("/api/fridge")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()["items"], ["鸡蛋", "西兰花", "番茄"])

    def test_empty_add_preserves_existing_inventory(self):
        self.client.post("/api/fridge/set", data={"items": "鸡蛋,西兰花"})
        response = self.client.post("/api/fridge/add", data={"items": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], ["鸡蛋", "西兰花"])

    def test_shopping_endpoint_deducts_owned_inventory(self):
        response = self.client.get("/api/shopping/list", params={
            "dishes": "番茄炒蛋",
            "inventory": "鸡蛋,食用油",
        })

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("鸡蛋", body["main"])
        self.assertNotIn("食用油", body["seasoning"])
        self.assertIn("番茄", body["main"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
