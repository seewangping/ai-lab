"""全局测试夹具：放"每个用例都该遵守的规矩"，而不是某个文件的私事。

这里有两条规矩，每一条都是靠一个真实的失败换来的：

## 规矩一：测试绝不能依赖开发者本机真实的 .env

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

## 规矩二：测试绝不能把全局日志配置泄漏出去

这是第二次踩同一类坑（第一次是上面的 .env），换了个全局单例而已。

给 app 加 lifespan 之后，`with TestClient(app)` 会执行 setup_logging()，
而它是**改根 logger** 的：clear 掉所有 handler、挂上我们的 JSON handler、
把 level 设成 INFO。如果不管，后果会以下面几种方式在**别的**用例里冒出来：

  - 后续所有用例的日志都被渲染成 JSON 打到 stdout，测试输出被淹没
  - pytest 自己挂在 root 上的日志捕获 handler 被 clear 掉，
    依赖 caplog 的用例开始莫名其妙地"收不到日志"
  - 根 logger 的 level 从 WARNING 变成 INFO，日志量变化

注意这类污染**不会让用例变红**——它只是改变了环境，然后在别处以
"顺序相关"的形式表现出来。那种失败最难查，因为单跑那条用例是绿的。
所以这里按"用例前后快照并还原"来处理，而不是等到出问题再补。
"""

import logging
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


@pytest.fixture(autouse=True)
def _isolate_root_logging() -> Iterator[None]:
    """快照并还原根 logger 的 handler 与 level。

    用列表切片（root.handlers[:]）做快照而不是记录"我加了什么"：
    被测代码可能清空整个列表，此时"只删自己加的那个"是删不掉的。
    按整体还原才能覆盖"清空 + 重加"这种最粗暴的改动。

    只在真的变了的时候才写回，避免干扰 pytest 日志插件对 handler 的
    自行增删（它的 handler 是按引用移除的，我们多写一次不影响它）。
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level

    yield

    if root.handlers != saved_handlers:
        root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
