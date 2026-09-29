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
