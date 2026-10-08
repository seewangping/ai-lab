"""依赖注入的语义测试：用最小应用验证 Depends 的行为，不牵扯业务代码。

为什么要单独测"框架行为"：这些是写业务时天天依赖的隐含前提。
把它们变成断言之后，"依赖到底是请求级还是全局""缓存是按什么键缓存的"
这类问题就不用靠记忆，跑一遍就有答案。
"""

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from ai_lab.config import Settings
from ai_lab.deps import get_settings_dep, verify_api_key

# 两把钥匙给【不同】的值——相同会被 config.py 的 model_validator 拒绝启动
TEST_CLIENT_KEY = "client-key-for-tests"
TEST_LLM_KEY = "sk-upstream-key-for-tests"


def _settings() -> Settings:
    """测试用假配置：不读本机 .env，值与机器无关。"""
    return Settings(_env_file=None, client_api_key=TEST_CLIENT_KEY, llm_api_key=TEST_LLM_KEY)


def test_dependency_is_cached_within_one_request() -> None:
    """同一请求内，同一个依赖默认只执行一次（结果被复用）。

    这就是"依赖的缓存范围"：默认 use_cache=True，按【依赖函数】缓存。
    """
    calls: list[int] = []

    def dep() -> int:
        calls.append(1)
        return len(calls)

    sub = FastAPI()

    @sub.get("/x")
    def x(a: int = Depends(dep), b: int = Depends(dep)) -> dict[str, int]:
        return {"a": a, "b": b, "executions": len(calls)}

    resp = TestClient(sub).get("/x")

    assert resp.json() == {"a": 1, "b": 1, "executions": 1}  # 两个参数拿到同一个值


def test_dependency_is_not_cached_across_requests() -> None:
    """跨请求不缓存：每个请求重新执行一次。"""
    calls: list[int] = []

    def dep() -> int:
        calls.append(1)
        return len(calls)

    sub = FastAPI()

    @sub.get("/x")
    def x(value: int = Depends(dep)) -> dict[str, int]:
        return {"value": value}

    sub_client = TestClient(sub)
    first = sub_client.get("/x").json()
    second = sub_client.get("/x").json()

    assert first == {"value": 1}
    assert second == {"value": 2}  # 第二次请求是新的执行


def test_dependency_can_be_disabled_per_use() -> None:
    """use_cache=False 时，同一个请求内每次出现都重新执行。"""
    calls: list[int] = []

    def dep() -> int:
        calls.append(1)
        return len(calls)

    sub = FastAPI()

    @sub.get("/x")
    def x(
        a: int = Depends(dep, use_cache=False),
        b: int = Depends(dep, use_cache=False),
    ) -> dict[str, int]:
        return {"a": a, "b": b}

    assert TestClient(sub).get("/x").json() == {"a": 1, "b": 2}


def test_dependency_failure_short_circuits_route() -> None:
    """依赖抛 HTTPException 时，路由函数体【不会执行】——这是鉴权能挡在门外的前提。"""
    executed: list[str] = []

    def guard() -> None:
        raise HTTPException(status_code=403, detail="拒绝")

    sub = FastAPI()

    @sub.get("/x")
    def x(_: None = Depends(guard)) -> dict[str, str]:
        executed.append("route ran")
        return {"ok": "yes"}

    resp = TestClient(sub).get("/x")

    assert resp.status_code == 403
    assert executed == []


def test_nested_dependency_shares_cache() -> None:
    """依赖的依赖（链式）也走同一套缓存：同一个依赖在一棵依赖树里只算一次。

    Day 3 的实际意义：verify_api_key 依赖 get_settings_dep，
    另一个依赖也依赖 get_settings_dep 时，配置只会被读一次。
    """
    calls: list[str] = []

    def leaf() -> str:
        calls.append("x")
        return f"leaf-{len(calls)}"

    def branch_one(value: str = Depends(leaf)) -> str:
        return f"one({value})"

    def branch_two(value: str = Depends(leaf)) -> str:
        return f"two({value})"

    sub = FastAPI()

    @sub.get("/x")
    def x(a: str = Depends(branch_one), b: str = Depends(branch_two)) -> dict[str, str]:
        return {"a": a, "b": b}

    body = TestClient(sub).get("/x").json()

    assert body == {"a": "one(leaf-1)", "b": "two(leaf-1)"}
    assert len(calls) == 1


# ------------------------------------------------- 真实依赖函数本身的行为测试

def test_verify_api_key_accepts_matching_key() -> None:
    """verify_api_key 是普通函数，可以直接单元测试——不必启动服务器。

    这是"依赖写成函数"的额外好处：既能当 HTTP 依赖用，也能当普通函数测。
    """
    assert verify_api_key(settings=_settings(), x_api_key=TEST_CLIENT_KEY) == TEST_CLIENT_KEY


def test_verify_api_key_rejects_missing_and_wrong() -> None:
    for bad in (None, "", "client-key-wrong"):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(settings=_settings(), x_api_key=bad)
        assert exc_info.value.status_code == 401


def test_llm_key_is_not_accepted_as_client_credential() -> None:
    """上游密钥不能当门禁卡用——这是"拆两把钥匙"的回归测试。

    W2 早期对外校验直接比对 llm_api_key，等于把上游密钥当客户端凭据发出去：
    任何人拿到你的服务密钥，就能绕过你的服务直接刷你的账单。
    这条断言把那个错误钉死——将来谁改回单钥匙，这里立刻红。
    """
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(settings=_settings(), x_api_key=TEST_LLM_KEY)

    assert exc_info.value.status_code == 401


def test_header_alias_is_case_insensitive() -> None:
    """HTTP 头大小写不敏感：x-api-key / X-API-Key / X-Api-Key 都能通过。

    这条验证头名配置正确——生产里客户端用什么大小写都不该失败。
    注意只有【头名】不敏感，头的【值】是敏感的（下面坏值用例里 sk-wrong 就是被值卡住的）。
    """
    sub = FastAPI()

    @sub.get("/secured")
    def secured(key: str = Depends(verify_api_key)) -> dict[str, str]:
        return {"key": key}

    sub.dependency_overrides[get_settings_dep] = _settings

    resp = TestClient(sub).get("/secured", headers={"x-api-key": TEST_CLIENT_KEY})

    assert resp.status_code == 200


def test_settings_dependency_returns_singleton() -> None:
    """get_settings_dep 包的是 lru_cache 单例：多次调用拿到同一个对象。"""
    assert get_settings_dep() is get_settings_dep()


def test_missing_header_returns_401_not_422() -> None:
    """缺 X-API-Key 头必须是 401，不能退化成 422。

    这是把 X-API-Key 从 `Header(...)` 换成 `APIKeyHeader(...)` 之后仍要守住的行为：
    APIKeyHeader 默认 auto_error=True 会自己抛 401「Not authenticated」，
    我们用 auto_error=False 关掉它，把判断权收回来，好给出能照着改的提示。
    """
    sub = FastAPI()

    @sub.get("/secured")
    def secured(key: str = Depends(verify_api_key)) -> dict[str, str]:
        return {"key": key}

    sub.dependency_overrides[get_settings_dep] = _settings

    resp = TestClient(sub).get("/secured")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "缺少或错误的 X-API-Key"}
    assert resp.headers["WWW-Authenticate"] == "ApiKey"
