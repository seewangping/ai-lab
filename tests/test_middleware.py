"""Day 6：请求上下文中间件的测试。

中间件是这个项目里唯一的"纯 ASGI"代码，所以测试也分两条路：

  直接调用它（传 scope / receive / send）
    能精确控制输入、看清它到底往 send 里放了什么。协议层的行为（消息字典长什么样）
    只有这样才测得到。

  通过 TestClient 打真实应用
    验证它在真实请求里确实生效（响应头、request_id 的一致性）。

只做后者的话，"非 HTTP 请求要放行""下游没发 response.start 就崩了"这类分支
永远测不到——它们在正常请求里不会发生。
"""

import asyncio
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_lab.middleware import (
    REQUEST_ID_LENGTH,
    ASGIApp,
    Message,
    Receive,
    RequestContextMiddleware,
    Scope,
    Send,
)

# --------------------------------------------------------------- 手工驱动 ASGI
#
# 类型别名直接从 middleware.py 导入，而不是在本文件里另写一份。
# 理由：ASGI 的消息契约（scope / receive / send 各自是什么形状）是被测对象的
# 一部分，测试另写一份就等于把契约抄了两遍——中间件改了契约，测试可能还"通过"。
# 第一版我图省事写成 Scope = dict[str, Any]，mypy 立刻报出 arg-type 不兼容：
# 真实的契约是 MutableMapping，因为它必须能被就地写入（中间件会往 scope 里塞 state）。


class Recorder:
    """把中间件发出去的消息收下来，方便逐步断言。"""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def start(self) -> Message:
        return next(m for m in self.messages if m["type"] == "http.response.start")

    def header(self, name: str) -> str | None:
        wanted = name.lower().encode("latin-1")
        for key, value in self.start.get("headers", []):
            if key.lower() == wanted:
                return value.decode("latin-1")
        return None

    def header_count(self, name: str) -> int:
        wanted = name.lower().encode("latin-1")
        return sum(1 for key, _ in self.start.get("headers", []) if key.lower() == wanted)


def _http_scope(method: str = "GET", path: str = "/x") -> Scope:
    return {"type": "http", "method": method, "path": path, "headers": []}


async def _noop_receive() -> Message:
    return {"type": "http.request"}


def _body_app(status: int = 200, body: bytes = b"ok") -> ASGIApp:
    """一个最小的 ASGI 应用：只发一条响应。"""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": status, "headers": [(b"x-a", b"1")]}
        )
        await send({"type": "http.response.body", "body": body})

    return app


def _run(mw: RequestContextMiddleware, scope: Scope) -> Recorder:
    recorder = Recorder()
    asyncio.run(mw(scope, _noop_receive, recorder))
    return recorder


# ------------------------------------------------------------------ 基本行为

def test_adds_request_id_to_response_header() -> None:
    """每个响应都带 X-Request-ID，值是 12 位十六进制。"""
    mw = RequestContextMiddleware(_body_app())
    recorder = _run(mw, _http_scope())

    request_id = recorder.header("X-Request-ID")
    assert request_id is not None
    assert len(request_id) == REQUEST_ID_LENGTH
    int(request_id, 16)  # 能按十六进制解析 → 纯 ASCII，可安全放进 HTTP 头
    # 只加一次，不要出现两个同名头
    assert recorder.header_count("X-Request-ID") == 1


def test_each_request_gets_a_different_id() -> None:
    """ID 必须每次都不一样，否则它就不是"定位某一次请求"的凭据。"""
    mw = RequestContextMiddleware(_body_app())

    ids = {_run(mw, _http_scope()).header("X-Request-ID") for _ in range(5)}

    assert len(ids) == 5


def test_existing_response_headers_are_preserved() -> None:
    """下游设的头不能被覆盖或丢掉。"""
    recorder = _run(RequestContextMiddleware(_body_app()), _http_scope())

    assert recorder.header("x-a") == "1"
    assert recorder.header("X-Request-ID") is not None


def test_does_not_add_duplicate_request_id_header() -> None:
    """下游已经设过 X-Request-ID 时不重复添加。

    错误处理器也会写这个头（未处理异常的 500 不经过本中间件），
    两侧都写就必须保证只有一份。
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-request-id", b"from-downstream")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    recorder = _run(RequestContextMiddleware(app), _http_scope())

    assert recorder.header("X-Request-ID") == "from-downstream"
    assert recorder.header_count("X-Request-ID") == 1


# ------------------------------------------------------------------ 访问日志

def test_logs_one_line_with_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    """每个请求一行日志，且字段是【独立的键】而不是拼在消息里的字符串。

    这是"结构化日志"的实际含义：method/path/http_status/duration_ms 能被
    日志系统直接查询、分组、画分位线；拼进 msg 就只能用眼睛看。
    """
    mw = RequestContextMiddleware(_body_app(status=201))

    with caplog.at_level(logging.INFO, logger="ai_lab.middleware"):
        _run(mw, _http_scope(method="POST", path="/chat"))

    records = [r for r in caplog.records if r.message == "请求完成"]
    assert len(records) == 1
    fields = records[0].__dict__
    assert fields["method"] == "POST"
    assert fields["path"] == "/chat"
    assert fields["http_status"] == 201
    assert isinstance(fields["duration_ms"], float)
    assert fields["duration_ms"] >= 0
    assert len(fields["request_id"]) == REQUEST_ID_LENGTH


def test_log_line_is_valid_json_with_extra_fields() -> None:
    """extra= 传进去的字段必须被 JsonFormatter 渲染成真正的 JSON 字段。

    这条同时守着 logging_setup.py 的一个改动：以前 JsonFormatter 只输出
    固定的四个键，现在会把 extra 里的业务字段一并带上。
    不这么做的话，中间件精心组织的字段会在渲染那一步被全部丢掉——
    "日志里字段都在"和"日志输出里字段都在"是两件事。
    """
    from ai_lab.logging_setup import JsonFormatter

    record = logging.LogRecord(
        name="ai_lab.middleware",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="请求完成",
        args=(),
        exc_info=None,
    )
    record.__dict__.update({"request_id": "abc123", "method": "GET", "http_status": 200})

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "请求完成"
    assert payload["request_id"] == "abc123"
    assert payload["method"] == "GET"
    assert payload["http_status"] == 200
    # 保留字段仍按原样渲染，没有因为"透传 extra"而被搅乱
    assert payload["level"] == "INFO"
    assert "time" in payload


def test_error_level_for_5xx(caplog: pytest.LogCaptureFixture) -> None:
    """5xx 用 ERROR 级，4xx/2xx 用 INFO ——这个区分决定告警有没有用。

    把 400 也打成 error，报警群很快就被"正常营业噪音"淹掉，
    真的故障反而没人看。
    """
    mw = RequestContextMiddleware(_body_app(status=503))

    with caplog.at_level(logging.INFO, logger="ai_lab.middleware"):
        _run(mw, _http_scope())

    record = next(r for r in caplog.records if r.message == "请求完成")
    assert record.levelno == logging.ERROR
    assert record.__dict__["http_status"] == 503


def test_two_requests_produce_two_log_lines(caplog: pytest.LogCaptureFixture) -> None:
    """一个请求一行，不能多也不能少（多行 = 日志量翻倍，少行 = 丢请求）。"""
    mw = RequestContextMiddleware(_body_app())

    with caplog.at_level(logging.INFO, logger="ai_lab.middleware"):
        _run(mw, _http_scope(path="/a"))
        _run(mw, _http_scope(path="/b"))

    paths = [r.__dict__["path"] for r in caplog.records if r.message == "请求完成"]
    assert paths == ["/a", "/b"]


# --------------------------------------------------------- 协议层与非 HTTP 分支

def test_non_http_scope_passes_through_untouched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WebSocket / lifespan 直通，不记访问日志、不改消息。

    这不是"忘了处理"：WS 一次连接只对应一条升级请求，
    用"一请求一行、带 duration_ms"的模型去套会得出误导性统计
    （一条活了 10 分钟的连接，耗时该记 0.001s 还是 600s？）。
    要观测 WS 得单独设计指标，见 ws.py 的 ConnectionManager。
    """
    called: list[Scope] = []

    async def ws_app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(scope)

    with caplog.at_level(logging.INFO, logger="ai_lab.middleware"):
        asyncio.run(
            RequestContextMiddleware(ws_app)(
                {"type": "websocket", "path": "/ws/chat"}, _noop_receive, Recorder()
            )
        )

    assert len(called) == 1
    assert caplog.records == []
    # 直通意味着连 state 都不碰——非 HTTP 路径不需要 request_id
    assert "state" not in called[0]


def test_exception_is_logged_and_reraised(caplog: pytest.LogCaptureFixture) -> None:
    """下游抛出未处理异常时：只记【一行】带栈的日志，然后继续往外抛。

    两个约束都是刻意的：

    ① **继续往外抛**。中间件不是终点，真正的兜底在最外层的 ServerErrorMiddleware，
       它负责给客户端一个 500 响应。在这里把异常吞掉，客户端会收到一个
       "连接被断开"而不是我们精心准备的统一 JSON。
    ② **只记一行**。实现时先写成"except 里打一条带栈的 + finally 里打一条访问日志"，
       结果一次失败打出两行——而"每个请求一行"是这套日志的前提：
       多一行意味着日志量翻倍、按 path 统计的口径被污染。
       改成把栈挂到访问日志那一行上（exc_info=caught）。
    """

    async def boom_app(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("下游炸了")

    mw = RequestContextMiddleware(boom_app)

    with caplog.at_level(logging.INFO, logger="ai_lab.middleware"):
        with pytest.raises(RuntimeError, match="下游炸了"):
            _run(mw, _http_scope())

    records = [r for r in caplog.records if r.name == "ai_lab.middleware"]
    assert len(records) == 1, [r.message for r in records]
    record = records[0]
    assert record.message == "请求异常退出"
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None  # 栈没丢，只是挂到了同一行上
    # 失败请求同样要有访问日志字段 —— 只记成功的请求，
    # 是"线上出问题时日志里什么都没有"的经典成因
    assert record.__dict__["http_status"] == 500
    assert record.__dict__["path"] == "/x"


def test_response_started_without_exception_logs_actual_status() -> None:
    """下游正常返回时日志里的状态码是真实值，不是默认的 500。"""

    async def half_app(scope: Scope, receive: Receive, send: Send) -> None:
        # 只发 start、不发 body 就返回：状态码已经被读到，不该继续显示 500
        await send({"type": "http.response.start", "status": 304, "headers": []})

    mw = RequestContextMiddleware(half_app)
    recorder = _run(mw, _http_scope())

    assert recorder.start["status"] == 304


# ------------------------------------------------------ 通过真实应用再验一遍

def test_real_app_response_carries_id_matching_the_body() -> None:
    """真实应用里，响应头的 request_id 与错误 body 里的必须是同一个值。

    两个值不一致的话，"用户截图里的 id"就查不到日志——那这个 id 等于没设计。
    """
    from ai_lab.api import app as real_app

    with TestClient(real_app) as client:
        resp = client.get("/no/such/path")

    body = resp.json()
    assert resp.status_code == 404
    assert resp.headers["X-Request-ID"] == body["request_id"]
