"""异常体系：把「可重试」和「不可重试」彻底分开。

为什么值得单独一个模块：重试逻辑（W3 调 LLM 时会写）只该问一件事
——「这个错误能不能再来一次」。判据集中在 retryable 标志上，
调用方不需要写一串 isinstance，加新错误类型也不用改重试代码。
"""


class AppError(Exception):
    """应用基础异常。业务侧统一捕获它，把上游错误挡在边界内。"""

    # 默认不可重试：宁可少重试，也别对着一个永远失败的请求狂刷
    retryable: bool = False

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RetryableError(AppError):
    """可重试错误的基类（超时、限流、5xx 都归这里）。"""

    retryable = True


class LLMTimeoutError(RetryableError):
    """上游模型超时：可能是网络抖动，也可能是负载高，值得退避重试。"""


class LLMRateLimitError(RetryableError):
    """上游限流（429）：必须退避后再试，硬重试只会加重限流。"""


class LLMServerError(RetryableError):
    """上游 5xx（500/502/503/504）：服务端问题，通常能自愈。"""


class LLMAuthError(AppError):
    """鉴权失败（401/403）：key 错、过期或无权限，重试一万次也没用。"""


class LLMInvalidRequestError(AppError):
    """请求本身不合法（400/422）：参数错、超长、内容违规。改请求才能解决。"""


class LLMInsufficientBalanceError(AppError):
    """账户欠费（402）：必须人工充值，重试无意义。"""


# HTTP 状态码 → 异常类。调 LLM 网关时转换一次，全项目复用同一套语义。
STATUS_CODE_ERRORS: dict[int, type[AppError]] = {
    400: LLMInvalidRequestError,
    401: LLMAuthError,
    402: LLMInsufficientBalanceError,
    403: LLMAuthError,
    422: LLMInvalidRequestError,
    429: LLMRateLimitError,
    500: LLMServerError,
    502: LLMServerError,
    503: LLMServerError,
    504: LLMServerError,
}


def error_from_status(status_code: int, message: str = "") -> AppError:
    """把 HTTP 状态码翻译成对应的异常实例。

    未收录的码按区间兜底：5xx 视为可重试，其他视为不可重试。
    """
    cls = STATUS_CODE_ERRORS.get(status_code)
    if cls is None:
        cls = LLMServerError if status_code >= 500 else LLMInvalidRequestError
    return cls(message or f"HTTP {status_code}", status_code=status_code)


def is_retryable(exc: BaseException) -> bool:
    """重试逻辑的唯一入口：只看标志位，不写 isinstance 链。"""
    return bool(getattr(exc, "retryable", False))


if __name__ == "__main__":
    print(f"{'状态码':<8}{'异常类':<28}{'可重试'}")
    for code in (400, 401, 402, 403, 418, 422, 429, 500, 502, 503, 504, 599):
        err = error_from_status(code)
        tag = "是" if is_retryable(err) else "否"
        print(f"{code:<8}{type(err).__name__:<28}{tag}")
