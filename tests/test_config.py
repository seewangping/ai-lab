"""配置层与异常体系的测试。

注意：测试绝不能依赖开发者本机真实的 .env —— 那样换台机器就红。
这里用 monkeypatch 控制环境变量，并用 _env_file=None 关掉文件读取。
"""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from ai_lab.config import Settings, get_settings
from ai_lab.errors import (
    AppError,
    LLMAuthError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    error_from_status,
    is_retryable,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """每个用例前后清空 lru_cache，避免用例之间互相污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """只提供两个必填项，让其余字段走默认值。

    注意两把钥匙给了【不同】的值：相同会被 model_validator 拒绝（见文件末尾的用例）。
    """
    monkeypatch.setenv("CLIENT_API_KEY", "dev-client-key")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")


def test_defaults_used_when_env_absent(api_key: None) -> None:
    """环境里没配的字段，取类里的默认值。"""
    settings = Settings(_env_file=None)

    assert settings.llm_timeout == 30.0
    assert settings.max_concurrent == 5
    assert settings.max_retries == 3


def test_env_var_overrides_default(api_key: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量优先级高于默认值，且字符串会被转成正确的类型。"""
    monkeypatch.setenv("LLM_TIMEOUT", "99")
    monkeypatch.setenv("MAX_RETRIES", "7")

    settings = Settings(_env_file=None)

    assert settings.llm_timeout == 99.0
    assert isinstance(settings.llm_timeout, float)
    assert settings.max_retries == 7
    assert isinstance(settings.max_retries, int)


def test_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """必填项缺失时必须启动即报错（fail fast），而不是等到运行时。

    两把钥匙都是必填——缺哪个都要在启动那一刻就说清楚，别留到线上。
    """
    monkeypatch.delenv("CLIENT_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    message = str(exc_info.value)
    assert "client_api_key" in message
    assert "llm_api_key" in message


def test_client_and_llm_keys_must_differ(monkeypatch: pytest.MonkeyPatch) -> None:
    """两把钥匙填成同一个值 → 拒绝启动。

    这条校验存在的理由：拆钥匙的全部价值在于"失效域不同"。
    填成一样，对外密钥泄露就同时等于上游密钥泄露，拆分就成了摆设——
    而配置写错这件事，只有在校验里才会被发现（文档里写一万句也没用）。
    """
    monkeypatch.setenv("CLIENT_API_KEY", "same-value")
    monkeypatch.setenv("LLM_API_KEY", "same-value")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "CLIENT_API_KEY 不能与 LLM_API_KEY 相同" in str(exc_info.value)


def test_get_settings_returns_same_instance(api_key: None) -> None:
    """lru_cache 生效：多次调用拿到的是同一个对象。"""
    assert get_settings() is get_settings()


@pytest.mark.parametrize(
    ("status_code", "expected_cls", "expected_retryable"),
    [
        (400, LLMInvalidRequestError, False),
        (401, LLMAuthError, False),
        (429, LLMRateLimitError, True),
        (500, LLMServerError, True),
        (503, LLMServerError, True),
    ],
)
def test_error_from_status_mapping(
    status_code: int, expected_cls: type[AppError], expected_retryable: bool
) -> None:
    err = error_from_status(status_code, "上游返回了错误")

    assert isinstance(err, expected_cls)
    assert err.status_code == status_code
    assert err.message == "上游返回了错误"
    assert is_retryable(err) is expected_retryable


@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [(418, False), (599, True)],
)
def test_error_from_status_fallback(status_code: int, expected_retryable: bool) -> None:
    """未收录的状态码按区间兜底：5xx 可重试，其余不可重试。"""
    err = error_from_status(status_code)

    assert is_retryable(err) is expected_retryable
    assert err.message == f"HTTP {status_code}"


def test_all_errors_are_app_errors() -> None:
    """所有自定义异常都继承 AppError，业务侧才能一处兜底。"""
    for code in (400, 401, 402, 403, 422, 429, 500, 502, 503, 504):
        assert isinstance(error_from_status(code), AppError)
