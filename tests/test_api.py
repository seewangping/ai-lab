"""API 层测试：用 TestClient 直接调 ASGI 应用，不需要真正启动服务器。

对照 W1：TestClient 对 API 的意义，等于 pytest 对纯函数的意义——
它让"服务能不能跑"从手工 curl 变成一条可以进质量门的断言。
"""

from fastapi.testclient import TestClient

from ai_lab.api import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_greet_returns_message() -> None:
    resp = client.get("/greet/world")

    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello, world!"}


def test_greet_with_excited_flag() -> None:
    """查询参数 excited=true 会改变输出——参数解析是框架做的，不是手写的。"""
    resp = client.get("/greet/world", params={"excited": True})

    assert resp.status_code == 200
    assert resp.json()["message"] == "Hello, world!!"


def test_unknown_route_returns_404() -> None:
    resp = client.get("/nope")

    assert resp.status_code == 404


def test_openapi_schema_lists_routes() -> None:
    """/docs 的底层是 OpenAPI schema：路由定义错了，文档也会跟着错。"""
    resp = client.get("/openapi.json")

    assert resp.status_code == 200
    assert "/health" in resp.json()["paths"]
    assert "/greet/{name}" in resp.json()["paths"]


# ---------------------------------------------------------------- Day 2: 校验

VALID_PAYLOAD: dict[str, object] = {
    "message": "糖尿病酮症酸中毒的补液原则是什么？",
    "session_id": "sess-20260929-001",
}


def test_chat_accepts_valid_payload() -> None:
    """合法请求 → 200，且字段按契约返回。"""
    resp = client.post("/chat", json=VALID_PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "sess-20260929-001"
    assert body["reply"].startswith("[stub]")
    assert body["model"] == "stub-model"  # 模型默认值也会出现在响应里
    assert body["latency_ms"] >= 0


def test_chat_response_model_filters_extra_fields() -> None:
    """response_model 是出站过滤器：路由多返回的内部键不会漏给客户端。

    路由里故意 return 了 _internal_trace_id / _raw_prompt，这里断言它们不存在。
    """
    resp = client.post("/chat", json=VALID_PAYLOAD)

    body = resp.json()
    assert "_internal_trace_id" not in body
    assert "_raw_prompt" not in body
    assert set(body) == {"reply", "session_id", "model", "latency_ms"}


def test_chat_rejects_empty_message() -> None:
    """空 message → 422（不是 200 也不是 500，校验在边界层完成）。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "message": ""})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "message"]


def test_chat_rejects_whitespace_only_message() -> None:
    """全空格会被 str_strip_whitespace 先 trim 再校验 → 依旧 422。

    若没有这条配置，"   " 长度为 3 就能通过 min_length=1，等于非空校验被绕过。
    """
    resp = client.post("/chat", json={**VALID_PAYLOAD, "message": "     "})

    assert resp.status_code == 422


def test_chat_rejects_missing_session_id() -> None:
    """缺必填字段 → 422，且错误信息精确指出是哪个字段。"""
    resp = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 422
    error = resp.json()["detail"][0]
    assert error["loc"] == ["body", "session_id"]
    assert error["type"] == "missing"


def test_chat_rejects_unknown_field() -> None:
    """extra="forbid"：拼错字段名或夹带额外字段 → 422，而不是静默忽略。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "sessionId": "typo"})

    assert resp.status_code == 422
    assert "sessionId" in str(resp.json()["detail"])


def test_chat_rejects_temperature_out_of_range() -> None:
    """ge / le 约束：temperature 超出 0~2 → 422。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "temperature": 3.5})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "temperature"]


def test_chat_rejects_bad_session_id_pattern() -> None:
    """pattern 约束：session_id 含非法字符（如空格、中文、斜杠）→ 422。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "session_id": "会话 001"})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "session_id"]


def test_chat_rejects_non_json_body() -> None:
    """Content-Type / JSON 语法错误同样由框架拦住 → 422，路由函数根本不会被调用。"""
    resp = client.post(
        "/chat",
        content="not-json",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 422


def test_history_accepts_valid_params() -> None:
    resp = client.get("/chat/history/sess-001", params={"limit": 5})

    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": "sess-001",
        "limit": 5,
        "offset": 0,
        "items": [],
    }


def test_history_rejects_bad_path_session_id() -> None:
    """Path 参数的 pattern 约束：路径里塞非法字符 → 422。"""
    resp = client.get("/chat/history/bad id")

    assert resp.status_code == 422


def test_history_rejects_limit_over_max() -> None:
    """Query 的 le 约束：limit 超过 100 → 422（防止一次拉全表）。"""
    resp = client.get("/chat/history/sess-001", params={"limit": 101})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["query", "limit"]


def test_history_rejects_negative_offset() -> None:
    resp = client.get("/chat/history/sess-001", params={"offset": -1})

    assert resp.status_code == 422
