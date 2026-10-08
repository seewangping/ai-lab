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

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

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


# 用官方的 APIKeyHeader 而不是手写 Header(alias=...)，差别只在【OpenAPI 文档】上：
#
#   手写 Header        → parameters 里多一个 X-API-Key，标注 required=False，
#                        security 段为空 → Swagger UI 里这个头显示成「选填、无锁」，
#                        用户不填就点 Try it out，撞上 401 却不知道为什么。
#                        （因为它有个 = None 默认值，框架认为它可选——文档与真实行为不符）
#   APIKeyHeader 方案  → 不占 parameters，改为生成 security + securitySchemes，
#                        Swagger UI 顶部出现「Authorize」按钮和锁图标，语义正确。
#
# auto_error=False 是关键：默认的 True 会自己抛 401「Not authenticated」，
# 我们就没法给出"缺少或错误的 X-API-Key"这种能照着改的提示，也拿不到恒定时间比较。
# 关掉它之后，缺失时这里收到 None，判断权回到我们手上。
_api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="调用方凭据。注意它与服务端调用上游 LLM 用的密钥是两把不同的钥匙。",
)

ApiKeyHeaderDep = Annotated[str | None, Depends(_api_key_scheme)]


def verify_api_key(
    settings: SettingsDep,
    x_api_key: ApiKeyHeaderDep,
) -> str:
    """鉴权依赖：校验请求头里的 X-API-Key，对不上就 401。

    依赖的返回值会被注入到路由参数里，所以它能"既鉴权又传值"。

    三个细节：
      1. 比对的是 settings.client_api_key（对外凭据），**不是** llm_api_key。
         后者是服务端调用上游 LLM 的钥匙，拿它对客户端做校验等于把上游
         密钥当门禁卡发出去——客户端一旦泄露，别人可以绕过你的服务直接刷账单。
      2. 用 secrets.compare_digest 而不是 ==：恒定时间比较，避免通过响应耗时
         逐位猜出密钥（时序攻击）。这是密钥比较的标准做法。
      3. 头名大小写由 HTTP 规范保证不敏感（x-api-key / X-API-Key 等价），
         但头的【值】是大小写敏感的。
    """
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.client_api_key):
        # 这里用 HTTPException 是"HTTP 层"的错误；业务层抛的是 W1 的 AppError，
        # 两者在 Day 6 由全局异常处理器统一成同一种 JSON 结构。
        #
        # WWW-Authenticate 的值是自定义 scheme token（没有 RFC 注册 ApiKey 这个方案，
        # 规范只要求它是合法 token，且比对时不区分大小写）。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少或错误的 X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key


# 鉴权后的调用方标识：路由声明这个参数 = 声明"本端点需要已鉴权"
ApiKeyDep = Annotated[str, Depends(verify_api_key)]
