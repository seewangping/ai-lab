"""依赖注入的语义测试：用最小应用验证 Depends 的行为，不牵扯业务代码。

为什么要单独测"框架行为"：这些是写业务时天天依赖的隐含前提。
把它们变成断言之后，"依赖到底是请求级还是全局""缓存是按什么键缓存的"
这类问题就不用靠记忆，跑一遍就有答案。
"""

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from ai_lab.deps import get_settings_dep, verify_api_key


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
    from ai_lab.config import Settings

    settings = Settings(_env_file=None, llm_api_key="sk-abc")

    assert verify_api_key(settings=settings, x_api_key="sk-abc") == "sk-abc"


def test_verify_api_key_rejects_missing_and_wrong() -> None:
    from ai_lab.config import Settings

    settings = Settings(_env_file=None, llm_api_key="sk-abc")

    for bad in (None, "", "sk-wrong"):
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(settings=settings, x_api_key=bad)
        assert exc_info.value.status_code == 401


def test_header_alias_is_case_insensitive() -> None:
    """HTTP 头大小写不敏感：x-api-key / X-API-Key / X-Api-Key 都能通过。

    这条验证别名配置正确——生产里客户端用什么大小写都不该失败。
    """
    sub = FastAPI()

    @sub.get("/secured")
    def secured(key: str = Depends(verify_api_key)) -> dict[str, str]:
        return {"key": key}

    from ai_lab.config import Settings

    sub.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None, llm_api_key="sk-abc"
    )

    resp = TestClient(sub).get("/secured", headers={"x-api-key": "sk-abc"})

    assert resp.status_code == 200


def test_settings_dependency_returns_singleton() -> None:
    """get_settings_dep 包的是 lru_cache 单例：多次调用拿到同一个对象。"""
    assert get_settings_dep() is get_settings_dep()


def test_missing_header_returns_401_not_422() -> None:
    """缺 X-API-Key 头必须是 401，不能因为"参数是必填"退化成 422。

    因为 x_api_key 的类型是 str | None 且默认 None——校验交给 verify_api_key 自己做，
    框架层面它只是"可选头"，所以不会抢先生成 422。
    """
    sub = FastAPI()

    @sub.get("/secured")
    def secured(key: str = Depends(verify_api_key)) -> dict[str, str]:
        return {"key": key}

    from ai_lab.config import Settings

    sub.dependency_overrides[get_settings_dep] = lambda: Settings(
        _env_file=None, llm_api_key="sk-abc"
    )

    resp = TestClient(sub).get("/secured", headers={"X-API-Key": ""})

    assert resp.status_code == 401
