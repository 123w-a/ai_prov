"""健康探针的 HTTP 契约测试，不启动完整应用和模型预热线程。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import health_route


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health_route.router)
    return TestClient(app)


class HealthRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = _client()

    def test_live_is_independent_from_readiness_dependencies(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ready_returns_200_when_all_local_checks_pass(self):
        with (
            patch.object(health_route, "DATA_DIR", self.root / "data"),
            patch.object(health_route, "SESSIONS_DIR", self.root / "sessions"),
            patch.object(health_route, "_model_key_configured", return_value=True),
            patch.object(health_route, "_rag_index_status", return_value=(True, "ok")),
        ):
            response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(all(response.json()["checks"].values()))

    def test_ready_returns_503_when_rag_index_is_missing(self):
        with (
            patch.object(health_route, "DATA_DIR", self.root / "data"),
            patch.object(health_route, "SESSIONS_DIR", self.root / "sessions"),
            patch.object(health_route, "_model_key_configured", return_value=True),
            patch.object(
                health_route,
                "_rag_index_status",
                return_value=(False, "Chroma 索引文件不存在或为空"),
            ),
        ):
            response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertFalse(response.json()["checks"]["rag_index"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
