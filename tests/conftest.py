"""全局测试夹具：放"每个用例都该遵守的规矩"，而不是某个文件的私事。

这里只有一条规矩，但它是靠一个真实的失败换来的：

    测试绝不能依赖开发者本机真实的 .env

原先 test_config.py 里有一个 autouse 的 _clear_settings_cache，
test_deps.py 里那条 `get_settings_dep() is get_settings_dep()` 没有。
结果在开发机上跑是绿的（因为存在 .env 文件），一旦换到干净环境
（CI 容器里没有 .env）就直接失败：

    pydantic_core.ValidationError: 2 validation errors ...
    client_api_key Field required / llm_api_key Field required

**这类问题只有"换个干净环境跑一遍"才会暴露**——本机把 .env 藏起来
再跑 pytest 就能复现。所以两条规矩都收在这里：

  1. 每个用例前后清空 Settings 的 lru_cache（它是全局单例，
     不清理就会沿着用例顺序传播状态，制造"单跑绿、全跑红"的幽灵失败）
  2. 需要真实 get_settings() 的用例，自己用 monkeypatch 给足必填项
     ——不要指望环境里恰好有一份 .env
"""

from collections.abc import Iterator

import pytest

from ai_lab.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """每个用例前后清空 lru_cache，避免用例之间互相污染。

    为什么前后都要清：用例内部可能用 monkeypatch 造了一份配置并让它进了缓存，
    monkeypatch 在用例结束时会把环境变量还原，**但缓存里那份 Settings 不会自动消失**
    ——它会带着测试环境的值泄漏给下一个用例。前面清是为了不受上一个人影响，
    后面清是为了不影响下一个人。
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
