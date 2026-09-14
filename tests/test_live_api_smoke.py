"""真实服务冒烟测试：默认跳过，仅显式设置 RUN_LIVE_API_SMOKE=1 时运行。"""

from __future__ import annotations

import json
import os
import unittest
import urllib.error
import urllib.request
import uuid


BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
RUN_LIVE_API_SMOKE = os.getenv("RUN_LIVE_API_SMOKE") == "1"


def _request(
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], bytes]:
    data = None
    headers: dict[str, str] = {}
    if fields is not None:
        boundary = f"----codex-smoke-{uuid.uuid4().hex}"
        body = []
        for key, value in fields.items():
            body.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            )
        body.append(f"--{boundary}--\r\n")
        data = "".join(body).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif method == "POST":
        data = b""
        headers["Content-Length"] = "0"

    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(
            f"{method} {path} 返回 HTTP {exc.code}: {detail[:500]}"
        ) from exc


def _json_request(
    path: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    status, _, body = _request(
        path,
        method=method,
        fields=fields,
        timeout=timeout,
    )
    return status, json.loads(body.decode("utf-8"))


def _parse_sse_events(body: bytes) -> list[dict]:
    events: list[dict] = []
    text = body.decode("utf-8", errors="replace").replace("\r\n", "\n")
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw:
                events.append(json.loads(raw))
    return events


@unittest.skipUnless(
    RUN_LIVE_API_SMOKE,
    "设置 RUN_LIVE_API_SMOKE=1 后运行真实服务冒烟测试",
)
class LiveApiSmokeTest(unittest.TestCase):
    def test_health_live_and_ready(self):
        live_status, _, live_body = _request("/api/health/live", timeout=5)
        self.assertEqual(live_status, 200)
        self.assertEqual(json.loads(live_body.decode("utf-8"))["status"], "ok")

        ready_status, _, ready_body = _request("/api/health/ready", timeout=10)
        ready = json.loads(ready_body.decode("utf-8"))
        self.assertEqual(ready_status, 200)
        self.assertEqual(ready["status"], "ok")
        self.assertTrue(all(ready["checks"].values()), ready["checks"])

    def test_text_chat_sse_reaches_finish(self):
        _, created = _json_request("/api/sessions", method="POST")
        session_id = created["session"]["session_id"]
        self.addCleanup(
            _request,
            f"/api/sessions/{session_id}",
            method="DELETE",
            timeout=5,
        )

        _, _, body = _request(
            "/api/chat",
            method="POST",
            fields={
                "session_id": session_id,
                "message": "请用一句话告诉我今晚吃什么。",
                "turn_id": uuid.uuid4().hex,
            },
            timeout=float(os.getenv("SMOKE_CHAT_TIMEOUT_S", "180")),
        )
        events = _parse_sse_events(body)
        errors = [event["error"] for event in events if event.get("error")]
        self.assertFalse(errors, f"SSE 返回错误事件：{errors}")
        self.assertTrue(
            any(event.get("finish") for event in events),
            f"SSE 未出现 finish 事件：{events[-3:]}",
        )

    def test_image_cancel_contract(self):
        status, body = _json_request(
            "/api/chat/cancel-image",
            method="POST",
            fields={
                "session_id": f"smoke_{uuid.uuid4().hex}",
                "turn_id": uuid.uuid4().hex,
                "keep_text": "false",
            },
            timeout=5,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["code"], 200)
        self.assertFalse(body["data"]["keep_text"])

    def test_allergen_guardrail_contract(self):
        from allergen_rules import audit_allergens, normalize_allergens

        _, profile = _json_request("/api/profile", timeout=5)
        members = profile["data"]["family"]["members"]
        allergens = [
            allergen
            for member in members
            for allergen in member["profile"].get("allergens") or []
        ]
        resolved = {
            code
            for allergen in allergens
            for code in normalize_allergens(allergen, log_unresolved=False)
        }
        probes = {
            "crustacean": "虾仁炒蛋",
            "fish": "清蒸鲈鱼",
            "egg": "鸡蛋羹",
            "milk": "奶油意面",
            "peanut": "花生拌面",
            "soy": "麻婆豆腐",
            "gluten": "面条",
            "nuts": "腰果虾仁",
            "sesame": "麻酱拌面",
            "mollusc": "蒜蓉扇贝",
            "sulphite": "亚硫酸盐处理果干",
        }
        checked = sorted(resolved & set(probes))
        if not checked:
            self.skipTest("当前画像没有可验证的过敏原，未修改用户画像")

        for code in checked:
            with self.subTest(code=code):
                violations = audit_allergens(
                    probes[code],
                    allergens,
                    use_optional=True,
                )
                self.assertTrue(violations, f"{code} 未命中确定性硬护栏")

    def test_local_rag_retrieval(self):
        from rag.retriever import search

        result = search(
            "高血压 低盐 膳食原则",
            n_results=2,
            use_hybrid=False,
            use_rerank=False,
        )
        self.assertFalse(result.error, f"RAG 检索失败：{result.error}")
        self.assertTrue(result.hits, "RAG 检索没有召回结果")
        self.assertTrue(all(hit.source for hit in result.hits), "RAG 命中缺少来源")


if __name__ == "__main__":
    unittest.main(verbosity=2)
