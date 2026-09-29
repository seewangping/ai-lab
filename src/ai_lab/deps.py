"""依赖注入（Depends）：把「获得某个东西」和「使用某个东西」分开。

对照 C#：
  最接近的是 ASP.NET Core 的构造函数注入 / [FromServices]。两点不同：
    1. FastAPI 的依赖是【函数级】声明的，写在参数上——不需要 Controller 构造器，
       也就不会有"某个 Controller 依赖了 8 个服务"那种臃肿。
    2. 依赖的解析发生在【每个请求】上，并且默认在同一请求内【缓存】（见
       tests/test_deps.py 里的实测：同请求内只执行一次，跨请求重新执行）。

为什么要有这一层：路由函数不应该关心"配置从哪来""密钥怎么校验"，
它只声明"我需要一个 Settings""我需要一个已鉴权的调用方"，然后专心写业务。
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ai_lab.config import Settings, get_settings


def get_settings_dep() -> Settings:
    """把全局单例包一层依赖函数——关键价值是【可被替换】。

    测试里一句
        app.dependency_overrides[get_settings_dep] = lambda: Settings(_env_file=None, ...)
    就能把真实配置换成假配置：不用准备 .env、不受本机环境影响、也不污染其他用例。
    （这就是 W1 待补清单里「monkeypatch 隔离」在 API 层的对应解法。）
    """
    return get_settings()


# Annotated 写法是新版 FastAPI 的推荐姿势：类型 + 依赖来源写在同一个地方。
# 老写法 `settings: Settings = Depends(get_settings_dep)` 等价，但类型与默认值混在一起。
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def verify_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """鉴权依赖：校验请求头里的 X-API-Key，对不上就 401。

    依赖的返回值会被注入到路由参数里，所以它能"既鉴权又传值"。

    两个细节：
      1. `Header(alias="X-API-Key")`——HTTP 头是大小写不敏感的，alias 只是显式写清
         名字（不写 alias 时 FastAPI 会把参数名 x_api_key 自动转成 x-api-key）。
      2. 用 secrets.compare_digest 而不是 ==：恒定时间比较，避免通过响应耗时
         逐位猜出密钥（时序攻击）。这是密钥比较的标准做法。
    """
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.llm_api_key):
        # 这里用 HTTPException 是"HTTP 层"的错误；业务层抛的是 W1 的 AppError，
        # 两者在 Day 6 由全局异常处理器统一成同一种 JSON 结构。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或错误的 X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key


# 鉴权后的调用方标识：路由声明这个参数 = 声明"本端点需要已鉴权"
ApiKeyDep = Annotated[str, Depends(verify_api_key)]
