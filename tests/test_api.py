"""API 层测试：用 TestClient 直接调 ASGI 应用，不需要真正启动服务器。

对照 W1：TestClient 对 API 的意义，等于 pytest 对纯函数的意义——
它让"服务能不能跑"从手工 curl 变成一条可以进质量门的断言。

Day 3 起，本文件的测试【完全不碰本机 .env】：
用 app.dependency_overrides 把 get_settings_dep 换成假配置，
用固定的 X-API-Key 过鉴权。换台机器、换个人跑，结果一样。
"""

import logging
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_lab.api import app
from ai_lab.config import Settings
from ai_lab.deps import get_settings_dep

client = TestClient(app)

# 测试用的假配置：所有值都是显式给的，_env_file=None 表示"别去读 .env"
# 两把钥匙给不同的值——对外凭据（客户端的门禁卡）与上游凭据（服务端的钥匙）必须分开
TEST_API_KEY = "client-key-for-tests"
TEST_LLM_KEY = "sk-upstream-key-for-tests"
TEST_MAX_CONCURRENT = 2


def _fake_settings() -> Settings:
    return Settings(
        _env_file=None,
        client_api_key=TEST_API_KEY,
        llm_api_key=TEST_LLM_KEY,
        llm_base_url="https://fake.example/v1",
        llm_timeout=5.0,
        max_concurrent=TEST_MAX_CONCURRENT,
        max_retries=1,
    )


@pytest.fixture(autouse=True)
def fake_settings() -> Iterator[None]:
    """把配置依赖替换成假实现（autouse = 每个用例自动生效）。

    这就是 FastAPI 可测性的核心：真实依赖与测试替身之间只差一行替换，
    路由代码一个字都不用改。
    """
    app.dependency_overrides[get_settings_dep] = _fake_settings
    yield
    app.dependency_overrides.clear()


def _validation_errors(resp: httpx.Response) -> list[dict[str, object]]:
    """取 422 响应里逐条的校验明细。

    Day 6 统一了错误体，结构变了：
        before  {"detail": [{"loc": [...], "type": ..., "msg": ...}]}
        after   {"error": "validation_error", "detail": "请求参数有 1 处不合法",
                 "retryable": false, "request_id": "...", "errors": [{"loc": ...}]}

    即 detail 从"结构化列表"变成"一句给人看的摘要"，结构化明细挪到 errors 键。
    这一改动把所有旧的 `resp.json()["detail"][0]` 断言都打红了——**那正是它该有的效果**：
    错误结构是客户端依赖的契约，改契约就该让测试红一次，而不是悄悄变了没人知道。

    因此这个 helper 顺带断言了外壳本身（error 码 + errors 存在），
    后面每个用例只需关心"哪几个字段错了"。
    """
    body = resp.json()
    assert body["error"] == "validation_error", body
    assert isinstance(body["errors"], list)
    return body["errors"]


AUTH = {"X-API-Key": TEST_API_KEY}


def test_health_returns_ok() -> None:
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_does_not_require_api_key() -> None:
    """存活探针必须免鉴权：容器编排探活时不会带业务密钥。"""
    resp = client.get("/health")

    assert resp.status_code == 200


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
    """合法请求 + 正确密钥 → 200，且字段按契约返回。"""
    resp = client.post("/chat", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "sess-20260929-001"
    assert body["reply"].startswith("[stub]")
    assert body["model"] == "stub-model"  # 模型默认值也会出现在响应里
    assert body["latency_ms"] >= 0


def test_chat_uses_injected_settings() -> None:
    """响应里出现的是【假配置】的 max_concurrent，证明依赖替换真的生效了。

    这条同时是"配置驱动的行为可以被测试"的证据——不用碰真实 .env。
    """
    resp = client.post("/chat", json=VALID_PAYLOAD, headers=AUTH)

    assert f"max_concurrent={TEST_MAX_CONCURRENT}" in resp.json()["reply"]


def test_chat_response_model_filters_extra_fields() -> None:
    """response_model 是出站过滤器：路由多返回的内部键不会漏给客户端。

    路由里故意 return 了 _internal_trace_id / _raw_prompt，这里断言它们不存在。
    """
    resp = client.post("/chat", json=VALID_PAYLOAD, headers=AUTH)

    body = resp.json()
    assert "_internal_trace_id" not in body
    assert "_raw_prompt" not in body
    assert set(body) == {"reply", "session_id", "model", "latency_ms"}


def test_chat_rejects_empty_message() -> None:
    """空 message → 422（不是 200 也不是 500，校验在边界层完成）。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "message": ""}, headers=AUTH)

    assert resp.status_code == 422
    assert _validation_errors(resp)[0]["loc"] == ["body", "message"]


def test_chat_rejects_whitespace_only_message() -> None:
    """全空格会被 str_strip_whitespace 先 trim 再校验 → 依旧 422。

    若没有这条配置，"   " 长度为 3 就能通过 min_length=1，等于非空校验被绕过。
    """
    resp = client.post("/chat", json={**VALID_PAYLOAD, "message": "     "}, headers=AUTH)

    assert resp.status_code == 422


def test_chat_rejects_missing_session_id() -> None:
    """缺必填字段 → 422，且错误信息精确指出是哪个字段。"""
    resp = client.post("/chat", json={"message": "hi"}, headers=AUTH)

    assert resp.status_code == 422
    error = _validation_errors(resp)[0]
    assert error["loc"] == ["body", "session_id"]
    assert error["type"] == "missing"


def test_chat_rejects_unknown_field() -> None:
    """extra="forbid"：拼错字段名或夹带额外字段 → 422，而不是静默忽略。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "sessionId": "typo"}, headers=AUTH)

    assert resp.status_code == 422
    assert "sessionId" in str(_validation_errors(resp))


def test_chat_rejects_temperature_out_of_range() -> None:
    """ge / le 约束：temperature 超出 0~2 → 422。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "temperature": 3.5}, headers=AUTH)

    assert resp.status_code == 422
    assert _validation_errors(resp)[0]["loc"] == ["body", "temperature"]


def test_chat_rejects_bad_session_id_pattern() -> None:
    """pattern 约束：session_id 含非法字符（如空格、中文、斜杠）→ 422。"""
    resp = client.post("/chat", json={**VALID_PAYLOAD, "session_id": "会话 001"}, headers=AUTH)

    assert resp.status_code == 422
    assert _validation_errors(resp)[0]["loc"] == ["body", "session_id"]


def test_chat_rejects_non_json_body() -> None:
    """Content-Type / JSON 语法错误同样由框架拦住 → 422，路由函数根本不会被调用。"""
    resp = client.post(
        "/chat",
        content="not-json",
        headers={**AUTH, "Content-Type": "application/json"},
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------- Day 3: 鉴权

def test_chat_without_api_key_returns_401() -> None:
    """缺密钥 → 401，且响应体是统一错误结构（不是 traceback）。

    Day 6 之后这里断言得比以前更多：不只看"说了什么"，还看外壳对不对——
    error 码、retryable、request_id 三样都在，客户端才可能只用一套解析逻辑。
    """
    resp = client.post("/chat", json=VALID_PAYLOAD)

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "unauthorized"
    assert body["detail"] == "缺少或错误的 X-API-Key"
    # 401 是调用方的问题，不该被当成"待会儿再试"
    assert body["retryable"] is False
    # request_id 有值 —— 它同时出现在响应头和访问日志里，是"用户截图 → 我能定位"的那根线
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]
    assert resp.headers["WWW-Authenticate"] == "ApiKey"


def test_chat_with_wrong_api_key_returns_401() -> None:
    resp = client.post("/chat", json=VALID_PAYLOAD, headers={"X-API-Key": "sk-wrong"})

    assert resp.status_code == 401


def test_upstream_llm_key_is_not_a_valid_client_credential() -> None:
    """拿上游 LLM 的密钥当 X-API-Key 用 → 401。

    这条是"两把钥匙"的安全回归测试。曾经的实现直接比对 llm_api_key，
    那时的行为是：把服务端调 DeepSeek 的密钥，当成发给客户端的门禁卡。
    后果不是"权限给多了"，而是绕过服务直接刷上游账单。
    """
    resp = client.post("/chat", json=VALID_PAYLOAD, headers={"X-API-Key": TEST_LLM_KEY})

    assert resp.status_code == 401


def test_openapi_declares_api_key_as_security_scheme() -> None:
    """OpenAPI 要把 X-API-Key 声明成 security scheme，不能是「选填参数」。

    区别很实际：手写 Header(alias=...) 时它会出现在 parameters 里且
    required=False、security 为空——Swagger UI 上表现为「不用填、没有锁」，
    用户照着文档点 Try it out 会撞上 401 却完全不知道为什么（文档与行为不符）。
    换成 APIKeyHeader 之后生成 security 段，Swagger UI 出现 Authorize 按钮。
    """
    spec = app.openapi()
    operation = spec["paths"]["/chat"]["post"]

    assert operation["security"] == [{"APIKeyHeader": []}]

    scheme = spec["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "header"
    assert scheme["name"] == "X-API-Key"
    # 顺带确认说明文案真的进了文档（Swagger UI 的 Authorize 弹窗里会显示）
    assert "上游" in scheme["description"]

    # 反向断言：它不该再以「必填标志为 False 的普通参数」形式出现
    header_params = [p for p in operation.get("parameters", []) if p["in"] == "header"]
    assert header_params == []


def test_auth_runs_before_body_validation() -> None:
    """鉴权失败时不会返回 422——依赖比请求体校验更早执行。

    意义：未授权调用方连"你的字段名拼错了"这种信息都拿不到，减少信息泄露。
    """
    resp = client.post("/chat", json={"garbage": True})

    assert resp.status_code == 401


def test_history_requires_api_key() -> None:
    """受保护的端点一个个都要挂上依赖——漏挂就是"忘记加 [Authorize]"，是真实事故来源。"""
    resp = client.get("/chat/history/sess-001")

    assert resp.status_code == 401


def test_history_accepts_valid_params() -> None:
    resp = client.get("/chat/history/sess-001", params={"limit": 5}, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": "sess-001",
        "limit": 5,
        "offset": 0,
        "items": [],
    }


def test_history_rejects_bad_path_session_id() -> None:
    """Path 参数的 pattern 约束：路径里塞非法字符 → 422。"""
    resp = client.get("/chat/history/bad id", headers=AUTH)

    assert resp.status_code == 422


def test_history_rejects_limit_over_max() -> None:
    """Query 的 le 约束：limit 超过 100 → 422（防止一次拉全表）。"""
    resp = client.get("/chat/history/sess-001", params={"limit": 101}, headers=AUTH)

    assert resp.status_code == 422
    assert _validation_errors(resp)[0]["loc"] == ["query", "limit"]


def test_history_rejects_negative_offset() -> None:
    resp = client.get("/chat/history/sess-001", params={"offset": -1}, headers=AUTH)

    assert resp.status_code == 422


# ------------------------------------------------------------------ 启动期接线
# 下面这组测试守的不是"某个函数对不对"，而是"应用有没有真的把它接上"。
# 它们的存在理由是一个实测出来的失败：setup_logging() 写得很对、测试也全绿，
# 但应用从不调用它——于是 Day 6 的"结构化日志"在真实服务里根本不存在。
# 这类"模块都对，就是没接线"的问题，只能由针对【接线】的断言来守。


def test_lifespan_installs_json_logging() -> None:
    """启动时必须把 JsonFormatter 挂到 root 上。

    ⚠️ 关键在 TestClient 的用法：必须写成 `with TestClient(app)`。
    不写 with 的话 Starlette 不会执行 lifespan，启动逻辑一行都不跑——
    这本身是个很容易踩的坑，下面的 test_plain_testclient_does_not_run_startup
    专门把它固定下来。
    """
    from ai_lab.logging_setup import JsonFormatter

    with TestClient(app):
        handlers = logging.getLogger().handlers
        assert any(isinstance(h.formatter, JsonFormatter) for h in handlers), (
            f"root 上没有 JsonFormatter，实际挂着：{handlers}"
        )


def test_plain_testclient_does_not_run_startup() -> None:
    """不写成 with 的 TestClient 不跑 lifespan —— 所以"日志装好了没"测不出来。

    这条测试是在给上面那条做注脚：它证明"不用 with 就测不到启动逻辑"，
    从而解释为什么这个 bug 能躲过整套测试。同时也提醒后来者：
    如果你的测试需要启动逻辑生效，用 with 把 TestClient 包起来。
    """
    from ai_lab.logging_setup import JsonFormatter

    TestClient(app).get("/health")  # 故意不用 with

    handlers = logging.getLogger().handlers
    assert not any(isinstance(h.formatter, JsonFormatter) for h in handlers)


def test_app_does_not_configure_logging_on_import() -> None:
    """import 这个模块本身不该有日志副作用。

    这是"模块顶层不放副作用"那条规矩的守卫：配置日志属于"运行服务"，
    不属于"导入模块"。一旦有人把 setup_logging() 挪回模块顶层，这条会红。

    同时它也解释了为什么 bug 会存在——测试里 import ai_lab.api 不会装日志，
    所以从测试角度看"没装"是正常状态，没人会去追问。
    """
    from ai_lab.logging_setup import JsonFormatter

    handlers = logging.getLogger().handlers
    assert not any(isinstance(h.formatter, JsonFormatter) for h in handlers)
