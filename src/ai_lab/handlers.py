"""全局异常处理器：把任何异常翻译成同一种 JSON 外壳。

Day 6 的验收标准只有一句：**客户端永远拿到统一格式的错误 JSON，不会看见裸 traceback。**
要做到这句，光写一个 try/except 不够——异常可能从四个不同的地方冒出来：

  ① 我们自己的 AppError（W1 那套异常体系，W3 接 LLM 后主要来源）
  ② 请求体校验失败（FastAPI 的 RequestValidationError）
  ③ HTTP 层错误（框架/路由抛的 StarletteHTTPException，例如 404）
  ④ 谁都没接住的意外（KeyError、TypeError……也就是"代码有 bug"）

前三个是"已知的、有语义的"，第四个是"未知的"。它们最终都必须长成同一个样子，
否则客户端就得写四套错误解析——而它一定会漏掉一套，通常是第四套。

## HTTPException 和自定义异常，到底什么时候用哪个

这是 Day 6 清单里的问题，答案可以用一句话概括：

    HTTPException 是"给 HTTP 客户端看的"，AppError 是"给业务看的"。

具体分工：

  - **AppError 及子类**：描述"业务上发生了什么"。它完全不认识 HTTP——
    errors.py 里没有 import fastapi，连状态码都是普通 int。好处是同一套异常
    在 CLI、消息队列消费者、批处理里都能用；只有"暴露成 HTTP 接口"时才需要一个
    翻译器（就是本文件）。
  - **HTTPException**：当"错误本身就是 HTTP 语义"时用它，比如 404 路由不存在、
    405 方法不对、401 缺 X-API-Key。这些概念在非 HTTP 场景里根本不存在，
    硬造成 AppError 反而是给自己加翻译负担。

判断标准：**这个错误在"没有 HTTP"的世界里还有意义吗？** 有 → AppError；没有 → HTTPException。

## 为什么"未处理异常"这一条最关键

其它三条只是统一格式，这一条才真正防事故：它保证**任何**漏网的 bug
都只会变成一句"服务器内部错误 + request_id"，而不是把栈、文件路径、
上游返回体甚至密钥一起回给客户端。细节去哪了？去日志了——用同一个 request_id 就能捞回来。
"""

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_lab.errors import AppError, describe, http_status_for, retry_after_for
from ai_lab.logging_setup import get_logger
from ai_lab.schemas import ValidationItem

log = get_logger(__name__)

# 未处理异常的对外文案。为什么必须是固定的、不含异常信息的常量：
# 异常字符串里可能带文件路径、SQL、上游返回体，甚至（最糟的）拼接进去的密钥。
INTERNAL_ERROR_DETAIL = "服务器内部错误，请稍后重试；如持续出现请提供 request_id 以便定位"

# HTTPException 的 detail 我们不转发给业务，但要给客户端一个机器可读的码
_HTTP_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}

# 这几个 HTTP 语义本身就在说"待会儿可能行"，所以给 Retry-After 是有意义的
_HTTP_RETRYABLE = frozenset({429, 503})


def request_id_of(request: Request) -> str | None:
    """从 request.state 取中间件放进去的请求 ID。

    取不到时返回 None 而不是抛异常：错误处理路径本身不该再制造错误。
    """
    return getattr(request.state, "request_id", None)


def _json_error(
    request: Request,
    *,
    status_code: int,
    error: str,
    detail: str,
    retryable: bool = False,
    retry_after: int | None = None,
    errors: list[ValidationItem] | None = None,
    # 类型写成 Mapping 而不是 dict：调用方传进来的 exc.headers 是 Mapping，
    # 而"能读的字典"本来就该按协议接收。参数类型能放宽就放宽、返回值类型能收窄就收窄。
    extra_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """所有错误响应的唯一出口。想改错误结构，只改这里。"""
    request_id = request_id_of(request)
    payload: dict[str, object] = {
        "error": error,
        "detail": detail,
        "retryable": retryable,
        "request_id": request_id,
    }
    if errors is not None:
        payload["errors"] = [item.model_dump() for item in errors]

    # 从 exc.headers 带过来的响应头必须原样保留。
    #
    # 这一条是被测试逼出来的：把 HTTPException 换成统一 JSON 之后，
    # deps.py 精心设的 WWW-Authenticate: ApiKey 头消失了。
    # 而 RFC 9110 要求 401 必须带 WWW-Authenticate —— 丢了它不只是少个字段，
    # 是让"客户端该怎么带上凭据"这件事失去了标准表达（浏览器弹密码框、
    # 一些 SDK 的自动重试都靠它）。
    # 教训：写"统一响应"的处理器时，最先丢掉的往往是头。
    headers: dict[str, str] = dict(extra_headers or {})

    # request_id 也回写进响应头。这里和 middleware.send_wrapper 里有重复，
    # 但两侧都写是有原因的：**未处理异常的 500 响应根本不经过中间件**。
    # Starlette 的层序是 ServerErrorMiddleware（最外）→ 用户中间件 →
    # ExceptionMiddleware → 路由；那一条 500 是 ServerErrorMiddleware 直接发出去的，
    # 中间件看不到它的 http.response.start，也就没机会加头。
    # 结果就是：恰恰在最需要凭据去查日志的那个响应上，头是缺的。
    # 两侧都写 + 中间件不重复添加（见 middleware.py），行为才统一。
    if request_id is not None:
        headers.setdefault("X-Request-ID", request_id)

    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """① 业务异常 → HTTP 状态码。

    状态码怎么定（详见 errors.py 的两级规则）：类上显式写了就用写的，
    否则按 retryable 兜底为 503 / 500。

    日志级别也按状态码分：5xx 是我们自己的问题，要能叫醒人；
    4xx 是调用方的问题，属于正常营业噪音。**这个区分直接决定告警有没有用**——
    把 400 也打成 error，报警群很快就没人看了。
    """
    status_code = http_status_for(exc)
    retry_after = retry_after_for(exc)

    level = logging.ERROR if status_code >= 500 else logging.WARNING
    log.log(
        level,
        "业务异常：%s",
        describe(exc),
        extra={
            "request_id": request_id_of(request),
            "http_status": status_code,
            "error_code": exc.code,
            "retryable": exc.retryable,
            "upstream_status": exc.status_code,
        },
    )

    return _json_error(
        request,
        status_code=status_code,
        error=exc.code,
        # 这里可以安全地把 we-authored 的消息给客户端：AppError 的 message
        # 是我们自己写的，不含栈信息。上游的原始报文应该在 error_from_status
        # 那一步就被消化掉，不要透传。
        detail=exc.message,
        retryable=exc.retryable,
        retry_after=retry_after,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """② 请求体/路径/查询参数校验失败 → 422。

    只保留 loc / type / msg 三个键：exc.errors() 里的 ctx 可能带不可 JSON 序列化的
    对象（比如自定义异常实例），整包塞进 JSONResponse 会直接抛 TypeError——
    于是"想报 422"变成"报 500"。
    """
    items = [
        ValidationItem(
            loc=list(err["loc"]),
            type=err["type"],
            msg=err["msg"],
        )
        for err in exc.errors()
    ]

    log.info(
        "参数校验失败：%d 处",
        len(items),
        extra={
            "request_id": request_id_of(request),
            "http_status": 422,
            "error_code": "validation_error",
        },
    )

    return _json_error(
        request,
        status_code=422,
        error="validation_error",
        detail=f"请求参数有 {len(items)} 处不合法",
        errors=items,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """③ HTTP 层错误（404 等）也穿同一件外套。

    注册的是 Starlette 的 HTTPException 而不是 FastAPI 的那个子类——
    路由没匹配上时框架抛的是父类，只注册子类的话 404 会绕过我们，长回默认的
    {"detail": "Not Found"}，那就前功尽弃了。
    """
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    retryable = exc.status_code in _HTTP_RETRYABLE

    log.info(
        "HTTP 异常：%s %s → %s",
        request.method,
        request.url.path,
        exc.status_code,
        extra={
            "request_id": request_id_of(request),
            "http_status": exc.status_code,
            "error_code": code,
        },
    )

    return _json_error(
        request,
        status_code=exc.status_code,
        error=code,
        detail=str(exc.detail),
        retryable=retryable,
        # 429/503 时给个等待提示；HTTPException 本身不表达"等多久"，用 1 秒兜底
        retry_after=1 if retryable else None,
        # 把异常自带的响应头（如 401 的 WWW-Authenticate）原样带出去
        extra_headers=exc.headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """④ 谁都没接住的意外 → 500，且**绝不回显异常内容**。

    这是四个处理器里唯一真正"兜底"的一个：它保证应用里任何一个 bug
    都不会把内部细节漏给客户端。代价是排障要靠日志——所以 request_id 必须打出来，
    让"用户截图"能对应到日志里的那条 traceback。
    """
    log.error(
        "未处理异常：%s %s",
        request.method,
        request.url.path,
        # exc_info=True 会把完整栈写进日志（JsonFormatter 会渲染成 exc 字段）
        exc_info=exc,
        extra={
            "request_id": request_id_of(request),
            "http_status": 500,
            "error_code": "internal_error",
            "exc_type": type(exc).__name__,
        },
    )

    return _json_error(
        request,
        status_code=500,
        error="internal_error",
        detail=INTERNAL_ERROR_DETAIL,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把四个处理器挂到应用上。在创建 app 之后调用一次。

    注册顺序不影响匹配：Starlette 是按异常的**继承链**逐个查找处理器的，
    找第一个命中的。所以 AppError 的子类会命中 AppError 处理器，
    而没被任何人认领的异常最后会落到 Exception 那一条。
    """
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    # Exception 这一条必须最后兜住：它是"其余所有"的意思
    app.add_exception_handler(Exception, unhandled_error_handler)
