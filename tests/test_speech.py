# tests/test_speech.py：语音识别接口测试（unittest，无需 pytest）
#
# 覆盖四类：
#   1) 未配置 DASHSCOPE_API_KEY → available=false（返回 503 信封）
#   2) 不支持的音频格式        → 400
#   3) 音频超过大小限制        → 413
#   4) 模拟 DashScope 返回文字 → 正确 text（返回 200 信封）
#
# 运行：uv run python tests/test_speech.py
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

# 把项目根目录加入 sys.path，确保 `import api.main_app` 等能找到
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from api.main_app import app


class TestSpeechRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _post(self, content=b"dummy-audio-bytes", name="t.wav", ctype="audio/wav"):
        return self.client.post(
            "/api/transcribe",
            files={"audio": (name, BytesIO(content), ctype)},
        )

    # 1) 未配置 DASHSCOPE_API_KEY → available=false（返回 503 信封）
    def test_no_key_returns_unavailable(self):
        with patch(
            "api.routes.speech_route.transcribe_audio",
            return_value={"available": False, "text": "", "message": "未配置 DASHSCOPE_API_KEY"},
        ):
            r = self._post()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], 503)
        self.assertFalse(body["data"]["available"])
        self.assertEqual(body["data"]["text"], "")

    # 2) 不支持的音频格式 → 400
    def test_unsupported_format_returns_400(self):
        r = self._post(name="bad.xyz", ctype="audio/xyz")
        self.assertEqual(r.status_code, 400)

    # 3) 音频超过大小限制 → 413
    def test_too_large_returns_413(self):
        big = b"x" * (11 * 1024 * 1024)  # 11MB > 默认 10MB
        r = self._post(content=big, name="big.wav", ctype="audio/wav")
        self.assertEqual(r.status_code, 413)

    # 4) 模拟 DashScope 返回文字 → 正确 text（返回 200 信封）
    def test_mock_dashscope_returns_text(self):
        with patch(
            "api.routes.speech_route.transcribe_audio",
            return_value={"available": True, "text": "我家有鸡蛋、番茄和青椒", "message": "语音识别成功"},
        ):
            r = self._post()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], 200)
        self.assertTrue(body["data"]["available"])
        self.assertEqual(body["data"]["text"], "我家有鸡蛋、番茄和青椒")


if __name__ == "__main__":
    unittest.main()
