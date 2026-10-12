"""演示端点：故意抛错，用来亲眼看到「统一错误结构」长什么样。

为什么值得存在：Day 6 的验收标准是"客户端永远不会看见裸 traceback"。
这句话没法靠读代码相信，只能靠打一发亲眼看。没有这些端点，想验证就得
临时往业务路由里插一行 `raise`，验完再删——那种验证不会被重复执行，
于是下次改动又会悄悄退化。

所以把它做成端点，并配一个开关（`DEMO_ROUTES_ENABLED`，见 config.py）：
留在代码里、可以在生产关掉、可以随时重跑。

## 三种错误的走法完全不同

| kind | 谁抛的 | 走哪个处理器 |
|---|---|---|
| unhandled | 我们的代码有个 bug（RuntimeError） | handlers.unhandled_error_handler |
| 其余 | 业务边的 AppError 子类 | handlers.app_error_handler |
| （顺带）参数不对 | FastAPI 的校验器 | handlers.validation_error_handler |

`unhandled` 那一条是重点：它**故意**在异常消息里塞一段"看起来很像密钥和路径"的文字，
然后断言响应里一个字都不该出现。这不是表演——真实事故就是这么泄露出去的：
一个上游 SDK 把请求体拼进异常消息，而这个异常被透传给了客户端。
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException

from ai_lab.config import Settings
from ai_lab.deps import SettingsDep
from ai_lab.errors import (
    AppError,
    LLMAuthError,
    LLMInsufficientBalanceError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)

router = APIRouter(tags=["demo"], include_in_schema=False)

# kind → 异常【类】。放类而不是实例：异常实例带着自己的 traceback 与 __traceback__ 状态，
# 复用同一个实例会让日志里的栈指向"第一次抛出"的位置，排查时被误导。
_ERROR_FACTORIES: dict[str, type[AppError]] = {
    "rate-limit": LLMRateLimitError,
    "timeout": LLMTimeoutError,
    "upstream": LLMServerError,
    "auth": LLMAuthError,
    "bad-request": LLMInvalidRequestError,
    "balance": LLMInsufficientBalanceError,
}

# 故意写成像"密钥 + 路径 + 行号"的内容。
# 它必须出现在日志里（排障要用），绝不能出现在响应里（泄露）。
_FAKE_INTERNAL_LEAK = (
    "内部细节不该外泄：LLM_API_KEY=sk-fake-should-never-appear-1234567 "
    "at /srv/app/src/ai_lab/stub_llm.py:42"
)


def require_demo_enabled(settings: SettingsDep) -> Settings:
    """开关关掉时，这些端点必须表现得像不存在。

    用 404 而不是 403：403 等于承认"这里有个东西但你不许用"，
    对一个本不该出现在生产里的调试入口，直接装作没有更合适。
    """
    if not settings.demo_routes_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return settings


DemoSettingsDep = Annotated[Settings, Depends(require_demo_enabled)]


@router.get("/demo/errors/{kind}", response_model=None)
def demo_error(kind: str, settings: DemoSettingsDep) -> NoReturn:
    """按 kind 抛一个相应的错误。用浏览器或 curl 打一下就能看到统一结构。

    两个注解上的细节：

    ① `response_model=None` 是必须的。FastAPI 会默认拿【返回类型标注】去生成
       response_model，而 NoReturn 不是合法的 Pydantic 字段类型，于是
       **在 import 阶段**就抛 FastAPIError：Invalid args for response field。
       注意它连测试都收集不了——不是运行时才炸，是模块导入就炸。
       语义上也确实该写：这个函数不产出自己的响应体，响应由异常处理器产出。

    ② 返回类型写 NoReturn 而不是 None：它对 mypy 说的是"这个函数不会正常返回"，
       比 None（"返回空"）准确。用 None 也跑得起来，但那是把语义说小了。

    用法：
        curl -s http://127.0.0.1:8000/demo/errors/rate-limit | jq
        curl -s -D- http://127.0.0.1:8000/demo/errors/rate-limit | grep -i retry-after
    """
    del settings  # 只是用来触发开关校验，函数体里用不到

    if kind == "unhandled":
        # 这不是 AppError，谁都没认领 -> 落到 Exception 兜底处理器
        raise RuntimeError(_FAKE_INTERNAL_LEAK)

    factory = _ERROR_FACTORIES.get(kind)
    if factory is None:
        raise HTTPException(
            status_code=404,
            detail=f"未知的错误类型 {kind!r}；可选：{', '.join([*_ERROR_FACTORIES, 'unhandled'])}",
        )

    # status_code 只填"上游返回的码"，与"我们回给客户端的码"是两回事——
    # 后者由 handlers.py 通过 http_status_for() 算出来。
    raise factory(f"演示用错误：{kind}", status_code=502)
