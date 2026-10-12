"""Day 6：统一错误响应 + 演示端点的测试。

这个文件回答一个问题：**"客户端永远拿到统一格式的错误 JSON"** 这句话，
能不能变成一组断言？能。下面每一条都对应一种"错误从哪来"。

四类来源各测一遍，因为它们走的是四个不同的处理器：
    ① AppError 及其子类        → app_error_handler
    ② 请求体校验失败            → validation_error_handler
    ③ 框架抛的 HTTP 异常（404） → http_exception_handler
    ④ 未处理的意外（bug）       → unhandled_error_handler

只测其中一类是不够的——真实事故往往就发生在"你没测的那一类"。
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from ai_lab.api import app as real_app
from ai_lab.config import Settings
from ai_lab.deps import get_settings_dep
from ai_lab.errors import (
    AppError,
    LLMAuthError,
    LLMInsufficientBalanceError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    RetryableError,
    describe,
    http_status_for,
    retry_after_for,
)
from ai_lab.handlers import INTERNAL_ERROR_DETAIL, register_exception_handlers

# 故意长得像"密钥 + 文件路径 + 行号"。它必须进日志，绝不许进响应——
# 真实泄露事故就是这么发生的：某个 SDK 把请求体拼进异常消息，异常又被透传给客户端。
FAKE_INTERNAL_SECRET = "LLM_API_KEY=sk-fake-should-never-appear-1234567"
FAKE_INTERNAL_PATH = "/srv/app/src/ai_lab/stub_llm.py:42"

# kind → 抛出的异常类。每个 kind 会走一遍"从异常到 HTTP 响应"的完整翻译。
_ERROR_CLASSES: dict[str, type[AppError]] = {
    "rate-limit": LLMRateLimitError,
    "timeout": LLMTimeoutError,
    "upstream": LLMServerError,
    "auth": LLMAuthError,
    "bad-request": LLMInvalidRequestError,
    "balance": LLMInsufficientBalanceError,
}


class _Body(BaseModel):
    """只用来触发校验失败的请求体。"""

    message: str = Field(min_length=1)
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


@pytest.fixture
def err_app() -> FastAPI:
    """一个最小应用，只挂异常处理器、不挂中间件。

    不挂中间件的理由：这样测出来的 request_id 一定是 None
    （因为没人往 scope["state"] 里放它）。**错误处理不该依赖中间件是否在场**——
    中间件在场时能给请求 ID，不在场时也必须能正常回错误，而不是自己先炸掉。
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{kind}")
    def raise_business_error(kind: str) -> None:
        cls = _ERROR_CLASSES[kind]
        raise cls(f"{kind} 演示错误", status_code=502)

    @app.post("/validate")
    def needs_valid_body(body: _Body) -> dict[str, str]:
        return {"message": body.message}

    @app.get("/boom")
    def raise_unexpected() -> None:
        # 注意消息里带着"密钥"和路径——下面会断言它们不出现在响应里
        raise RuntimeError(f"内部细节：{FAKE_INTERNAL_SECRET} at {FAKE_INTERNAL_PATH}")

    return app


@pytest.fixture
def err_client(err_app: FastAPI) -> Iterator[TestClient]:
    """raise_server_exceptions=False 是必需的。

    TestClient 默认会把应用里冒到最外层的异常**重新抛给测试**（而不是返回 500），
    于是"断言响应是统一 500"这件事根本无从下手。
    关掉它，才等价于真实服务器（uvicorn）的行为：给客户端一个 500，把栈留给自己。
    """
    with TestClient(err_app, raise_server_exceptions=False) as client:
        yield client


# ------------------------------------------------- ① 业务异常 → 状态码与响应头

@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_retryable", "expected_retry_after"),
    [
        # 上游限流：429 才是最贴切的状态码，别被兜底的 503 吞掉
        ("rate-limit", 429, True, "1"),
        # 超时：网关等不到响应 → 504
        ("timeout", 504, True, "1"),
        # 上游 5xx：故意没在类上写 http_status → 走兜底规则拿到 503
        ("upstream", 503, True, "1"),
        # 上游凭据无效：这是我们自己的配置事故，**不能把上游的 401 透传给客户端**
        ("auth", 500, False, None),
        # 请求本身不合法 → 400
        ("bad-request", 400, False, None),
        # 欠费：503 但不可重试 —— 这一条恰好证明 Retry-After 不是从状态码推出来的
        ("balance", 503, False, None),
    ],
)
def test_business_error_maps_to_status_code(
    err_client: TestClient,
    kind: str,
    expected_status: int,
    expected_retryable: bool,
    expected_retry_after: str | None,
) -> None:
    resp = err_client.get(f"/raise/{kind}")

    assert resp.status_code == expected_status
    body = resp.json()
    assert body["retryable"] is expected_retryable
    assert body["detail"] == f"{kind} 演示错误"
    # 外壳的四个键一个都不能少 —— 客户端靠它们写分支
    assert set(body) == {"error", "detail", "retryable", "request_id"}

    if expected_retry_after is None:
        assert "retry-after" not in resp.headers
    else:
        assert resp.headers["retry-after"] == expected_retry_after


def test_error_code_is_stable_and_machine_readable(err_client: TestClient) -> None:
    """error 字段是给程序读的稳定码，不是文案。

    这条断言的意义在于：客户端该写 `if body["error"] == "upstream_rate_limited"`，
    而不是去匹配 detail 里那句话——那句话随时会被改、被翻译。
    """
    assert err_client.get("/raise/rate-limit").json()["error"] == "upstream_rate_limited"
    assert err_client.get("/raise/auth").json()["error"] == "upstream_auth_failed"
    # 故意没写 http_status 的那个，错误码也照样要有
    assert err_client.get("/raise/upstream").json()["error"] == "upstream_error"


def test_request_id_is_none_without_middleware(err_client: TestClient) -> None:
    """没有中间件时，request_id 是 None —— 但错误响应依然完整，不该因此报错。"""
    body = err_client.get("/raise/timeout").json()

    assert body["request_id"] is None
    assert body["error"] == "upstream_timeout"


# ------------------------------------------------------- ② 校验失败 → 422

def test_validation_error_keeps_structured_details(err_client: TestClient) -> None:
    """422 的逐条明细必须保留在 errors 键里，不能被"统一"掉。

    统一的是【外壳】，不是把有用信息压成一句话。loc/type/msg 是前端
    标红具体输入框的依据，丢了它前端就只能弹一个笼统的提示。
    """
    resp = err_client.post("/validate", json={"message": "", "session_id": "会话 001"})

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_error"
    # detail 现在是一句人话摘要，不再是列表
    assert isinstance(body["detail"], str)
    # 结构化明细在 errors 里，两条都报出来了
    locations = [item["loc"] for item in body["errors"]]
    assert ["body", "message"] in locations
    assert ["body", "session_id"] in locations
    assert all(item["type"] and item["msg"] for item in body["errors"])


def test_validation_error_does_not_leak_ctx_objects(err_client: TestClient) -> None:
    """校验失败必须稳定返回 422，而不是因为序列化失败变成 500。

    Pydantic 的 errors() 里带 ctx，某些校验器的 ctx 会是不可 JSON 序列化的对象；
    整包塞进 JSONResponse 会抛 TypeError —— "想报 422"就变成了"报 500"。
    所以处理器只挑 loc/type/msg 三个键。
    """
    resp = err_client.post("/validate", json={})

    assert resp.status_code == 422
    assert set(resp.json()["errors"][0]) == {"loc", "type", "msg"}


def test_method_not_allowed_is_wrapped(err_client: TestClient) -> None:
    """405 也从框架抛出，同样要穿统一外套。"""
    resp = err_client.post("/raise/rate-limit")

    assert resp.status_code == 405
    assert resp.json()["error"] == "method_not_allowed"


def test_http_exception_is_wrapped_too(err_client: TestClient) -> None:
    """框架自己抛的 404/405 也要穿同一件外套。

    注册的是 Starlette 的 HTTPException 而不是 FastAPI 那个子类——
    路由没匹配上时抛的是父类，只注册子类的话 404 会绕过我们，
    长回 {"detail": "Not Found"}，客户端就得写第二套解析。
    """
    resp = err_client.get("/no/such/path")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "not_found"
    assert body["retryable"] is False


def test_http_exception_keeps_its_own_headers() -> None:
    """HTTPException 自带的响应头必须原样保留。

    这条是补出来的：把 HTTPException 换成统一 JSON 之后，
    `WWW-Authenticate: ApiKey` 消失了——**而 RFC 9110 要求 401 必须带它**。
    漏掉它不只是少个字段：客户端失去"该用哪种方式带凭据"的标准表达。
    写"统一响应"的处理器时，最先被弄丢的往往就是头。

    注意这里的头值必须是 ASCII。写这个测试时我先用了中文，
    得到 UnicodeEncodeError: 'latin-1' codec can't encode characters ——
    HTTP 头的值是 latin-1 编码的，放不下中文。这恰好解释了本项目的两个设计：
    request_id 用 hex（纯 ASCII）而不是随机中文；错误消息放在 JSON body 里
    而不是塞进响应头。**头是给协议用的，body 才是给人看的。**
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/need-auth")
    def need_auth() -> None:
        raise HTTPException(
            status_code=401,
            detail="缺少或错误的 X-API-Key",
            headers={"WWW-Authenticate": "ApiKey", "X-Custom": "keep-me"},
        )

    resp = TestClient(app).get("/need-auth")

    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "ApiKey"
    assert resp.headers["X-Custom"] == "keep-me"
    assert resp.json()["error"] == "unauthorized"


# --------------------------------------------- ④ 未处理异常 → 500（最关键的一条）

def test_unhandled_error_does_not_leak_internals(err_client: TestClient) -> None:
    """未处理的 bug → 统一 500，且**异常内容一个字都不许出现在响应里**。

    这是四个处理器里唯一真正防事故的一个。细节去哪了？去日志了（用 exc_info），
    靠同一个 request_id 就能捞回来。
    """
    resp = err_client.get("/boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body == {
        "error": "internal_error",
        "detail": INTERNAL_ERROR_DETAIL,
        "retryable": False,
        "request_id": None,
    }
    # 把整个响应体当字符串搜一遍——包括头，别只搜 JSON
    raw = resp.text + str(dict(resp.headers))
    assert FAKE_INTERNAL_SECRET not in raw
    assert "sk-fake-should-never-appear" not in raw
    assert FAKE_INTERNAL_PATH not in raw
    assert "RuntimeError" not in raw
    assert "Traceback" not in raw


def test_unhandled_error_is_logged_with_traceback(
    err_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """细节没给客户端，就必须给日志——否则这条 500 永远查不出来。

    一个"既不外泄也不记录"的兜底处理器，等于把事故从"泄露"变成"失联"。
    """
    with caplog.at_level("ERROR"):
        err_client.get("/boom")

    assert any("未处理异常" in r.message for r in caplog.records)
    record = next(r for r in caplog.records if "未处理异常" in r.message)
    # exc_info 不为空 = 完整栈被记下来了。这正是"细节不给客户端就得给日志"的落点：
    # 一个既不外泄也不记录的兜底处理器，等于把事故从"泄露"变成"失联"。
    assert record.exc_info is not None
    assert record.__dict__["exc_type"] == "RuntimeError"
    assert record.__dict__["error_code"] == "internal_error"
    assert record.__dict__["http_status"] == 500


def test_500_response_also_carries_request_id_header() -> None:
    """未处理异常的 500 也必须带 X-Request-ID 头 —— 这条差点漏掉。

    为什么它是特例：Starlette 的层序是

        ServerErrorMiddleware（最外） → 用户中间件 → ExceptionMiddleware → 路由

    未处理异常是被【最外层】接住并直接发响应的，**根本不经过用户中间件**，
    所以中间件那段"把 request_id 写进响应头"的代码对这条路径无效。
    实测症状：响应体里有 request_id，响应头里没有——
    偏偏在"用户要截图报障"的那个响应上，头是缺的。

    修法是让错误处理器也写这个头，并要求中间件不重复添加。
    """
    with _demo_client() as client:
        resp = client.get("/demo/errors/unhandled")

    assert resp.status_code == 500
    body = resp.json()
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]
    # 只应有一个同名头（两侧都写，但不能变成两个）
    assert len(resp.headers.get_list("X-Request-ID")) == 1


# ------------------------------------------------- ⑤ errors.py 的两个纯函数

def test_http_status_for_prefers_explicit_class_attribute() -> None:
    """类上写了就用写的。"""
    assert http_status_for(LLMRateLimitError("x")) == 429
    assert http_status_for(LLMTimeoutError("x")) == 504
    assert http_status_for(LLMAuthError("x")) == 500


def test_http_status_for_falls_back_to_retryable() -> None:
    """没写 http_status 时按 retryable 兜底：可重试 503，不可重试 500。

    兜底路径不是文档里的摆设：LLMServerError 就靠它拿到 503。
    这也是"把判据做成类属性"的好处——新增异常类型时不用去改任何一张映射表。
    """
    assert http_status_for(AppError("普通业务错误")) == 500
    assert http_status_for(RetryableError("可重试但没有具体状态码")) == 503
    # LLMServerError 是真实代码里走这条兜底的那个
    assert http_status_for(LLMServerError("上游 502")) == 503


def test_retry_after_only_for_retryable() -> None:
    """不可重试的错误一律不给 Retry-After —— 否则等于指挥客户端白等一轮。"""
    assert retry_after_for(LLMRateLimitError("限流")) == 1
    assert retry_after_for(LLMAuthError("key 错")) is None
    assert retry_after_for(AppError("未知")) is None
    # 欠费是 503 但不可重试：状态码像"待会儿再说"，语义上却不是
    assert retry_after_for(LLMInsufficientBalanceError("欠费")) is None


def test_describe_is_one_line_summary() -> None:
    """describe() 给日志用：类名 + 错误码 + 对客户端的码 + 可重试性 + 消息。"""
    text = describe(LLMRateLimitError("上游限流", status_code=429))

    assert "LLMRateLimitError" in text
    assert "upstream_rate_limited" in text
    assert "429" in text
    assert "可重试" in text
    assert "上游限流" in text
    assert "\n" not in text


# ------------------------------------------------- ⑥ 演示端点（走真实 app）

def _fake_settings(*, demo_routes_enabled: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        client_api_key="client-key-for-tests",
        llm_api_key="sk-upstream-key-for-tests",
        demo_routes_enabled=demo_routes_enabled,
    )


def _demo_client(*, demo_routes_enabled: bool = True) -> TestClient:
    real_app.dependency_overrides[get_settings_dep] = lambda: _fake_settings(
        demo_routes_enabled=demo_routes_enabled
    )
    # 同样需要关掉 raise_server_exceptions：unhandled 那一档就是要让响应变成 500
    return TestClient(real_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    real_app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("rate-limit", 429),
        ("timeout", 504),
        ("upstream", 503),
        ("auth", 500),
        ("bad-request", 400),
        ("balance", 503),
        ("unhandled", 500),
    ],
)
def test_demo_endpoint_exposes_every_error_shape(kind: str, expected_status: int) -> None:
    """演示端点把每一种错误都真的抛一遍。

    它的价值不是"功能"，而是让"客户端永远拿到统一结构"这句话可以被亲手验证，
    而不是只能读代码相信。有了它，验证这件事才可重复——
    临时插一行 raise 再删掉的做法，下次改动就没人再验了。
    """
    with _demo_client() as client:
        resp = client.get(f"/demo/errors/{kind}")

    assert resp.status_code == expected_status
    body = resp.json()
    assert set(body) >= {"error", "detail", "retryable", "request_id"}
    # 真实 app 挂了中间件，所以这次 request_id 必须有值
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_demo_unknown_kind_suggests_valid_options() -> None:
    """打错 kind → 404，并由 detail 列出可选值（别让调用方去翻源码）。"""
    with _demo_client() as client:
        resp = client.get("/demo/errors/not-a-kind")

    assert resp.status_code == 404
    assert "not-a-kind" in resp.json()["detail"]
    assert "rate-limit" in resp.json()["detail"]


def test_demo_routes_can_be_disabled() -> None:
    """开关关掉时必须表现为"不存在"。

    用 404 而不是 403：403 等于承认"这里有个东西但不给你用"，
    对一个本不该出现在生产的调试入口，直接装作没有更合适。
    """
    with _demo_client(demo_routes_enabled=False) as client:
        resp = client.get("/demo/errors/rate-limit")

    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_demo_routes_are_hidden_from_openapi() -> None:
    """演示端点不进 /docs —— 文档是给调用方看的，不该教人怎么让服务出错。"""
    with _demo_client() as client:
        spec = client.get("/openapi.json").json()

    assert "/demo/errors/{kind}" not in spec["paths"]
