"""异常体系：把「可重试」和「不可重试」彻底分开。

为什么值得单独一个模块：重试逻辑（W3 调 LLM 时会写）只该问一件事
——「这个错误能不能再来一次」。判据集中在 retryable 标志上，
调用方不需要写一串 isinstance，加新错误类型也不用改重试代码。

Day 6 又给这套体系加了两件东西，它们都是"类属性"而不是"映射表"：

  http_status         这个错误该以什么状态码返回给【调用方】
  retry_after_seconds 可重试时，告诉调用方等几秒再来

为什么放进类而不是在 handler 里写一张 dict 映射表：
  映射表离类太远，加新错误类型时容易忘（忘了就静默退回 500）；
  写成类属性，新异常在定义处就必须表态，而且继承默认值天然合理
  （RetryableError 的子类自动带上 503 + Retry-After 的兜底行为）。
"""


class AppError(Exception):
    """应用基础异常。业务侧统一捕获它，把上游错误挡在边界内。"""

    # 默认不可重试：宁可少重试，也别对着一个永远失败的请求狂刷
    retryable: bool = False

    # 面向【调用方】的稳定错误码。客户端按它写分支，不要按 detail 的文案分支
    # ——文案是给人看的，随时会改。
    code: str = "internal_error"

    # None 表示"没表态"，由 http_status_for() 按 retryable 兜底。
    # 显式写了就用写的（更贴切的状态码优先）。
    http_status: int | None = None

    # 只有可重试的错误才该给这个值：不可重试却回一个 Retry-After
    # 等于在骗客户端白等一轮。
    retry_after_seconds: int | None = None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        # 注意区分两个 status_code：
        #   上游返回的那个（属性 status_code，由 error_from_status 填入）
        #   我们要返回给客户端的那个（类属性 http_status，由 http_status_for 计算）
        # 它们经常不一样，这正是"边界层要翻译"的地方。
        self.status_code = status_code


class RetryableError(AppError):
    """可重试错误的基类（超时、限流、5xx 都归这里）。

    继承它就自动获得：retryable=True、HTTP 503（兜底）、Retry-After: 1。
    """

    retryable = True
    retry_after_seconds = 1


class LLMTimeoutError(RetryableError):
    """上游模型超时：可能是网络抖动，也可能是负载高，值得退避重试。"""

    code = "upstream_timeout"
    # 超时更像"网关等不到响应"，504 比兜底的 503 更准
    http_status = 504


class LLMRateLimitError(RetryableError):
    """上游限流（429）：必须退避后再试，硬重试只会加重限流。"""

    code = "upstream_rate_limited"
    # 429 是唯一一个"重试"含义明确的状态码，别被 503 吞掉：
    # 客户端看到 429 会自己退避，看到 503 可能立刻重试，反而把上游打得更死。
    http_status = 429


class LLMServerError(RetryableError):
    """上游 5xx（500/502/503/504）：服务端问题，通常能自愈。

    故意【不写】http_status —— 让它走 http_status_for() 的兜底规则拿到 503。
    这样兜底路径在真实代码里被走到，而不是只在文档里写着。
    """

    code = "upstream_error"


class LLMAuthError(AppError):
    """鉴权失败（401/403）：key 错、过期或无权限，重试一万次也没用。"""

    code = "upstream_auth_failed"
    # 关键：**不能把上游的 401 透传给调用方**。
    # 上游 401 说明"我们自己配的 key 有问题"，跟调用方一点关系都没有；
    # 透传 401 会让客户端以为自己的凭据错了，去改一个根本没错的东西。
    # 这是服务端的配置事故，就该老老实实报 5xx。
    http_status = 500


class LLMInvalidRequestError(AppError):
    """请求本身不合法（400/422）：参数错、超长、内容违规。改请求才能解决。"""

    code = "upstream_invalid_request"
    http_status = 400


class LLMInsufficientBalanceError(AppError):
    """账户欠费（402）：必须人工充值，重试无意义。"""

    code = "upstream_insufficient_balance"
    # 服务端暂时不可用且不会自愈，等人工处理；给 503 而不是 500，
    # 是为了让监控把"需要运营介入"和"程序有 bug"区分开。
    http_status = 503


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


# 兜底值：往上没表态时用。503 的语义（现在不行、待会儿可能行）恰好等于 retryable
FALLBACK_RETRYABLE_STATUS = 503
FALLBACK_STATUS = 500


def http_status_for(exc: AppError) -> int:
    """这个错误该以什么状态码返回给【调用方】。

    两级规则，顺序不能反：
      1. 类上显式写了 http_status → 用它（更贴切的状态码优先）
      2. 没写 → 按 retryable 兜底：可重试 503，不可重试 500

    注意这个值跟 exc.status_code（上游返回的码）是两回事。
    上游说 429，我们对客户端也说 429 才合理；但上游说 401（我们的 key 错了），
    我们对客户端必须说 500 —— 翻译是边界层的职责，不是转发。
    """
    if exc.http_status is not None:
        return exc.http_status
    return FALLBACK_RETRYABLE_STATUS if exc.retryable else FALLBACK_STATUS


def retry_after_for(exc: AppError) -> int | None:
    """需要让客户端"等几秒再来"时返回秒数，否则 None。

    对不可重试的错误一律返回 None：明明重试没用却回一个 Retry-After，
    等于指挥客户端白等一轮。
    """
    return exc.retry_after_seconds if exc.retryable else None


def describe(exc: AppError) -> str:
    """一行摘要，用于日志（不含 traceback）。"""
    retry = "可重试" if exc.retryable else "不可重试"
    return f"{type(exc).__name__}({exc.code}, {http_status_for(exc)}, {retry}): {exc.message}"


if __name__ == "__main__":
    print(f"{'上游码':<8}{'异常类':<30}{'错误码':<32}{'对客户端':<10}{'可重试':<8}{'Retry-After'}")
    for upstream in (400, 401, 402, 403, 418, 422, 429, 500, 502, 503, 504, 599):
        err = error_from_status(upstream)
        retry_after = retry_after_for(err)
        print(
            f"{upstream:<8}{type(err).__name__:<30}{err.code:<32}"
            f"{http_status_for(err):<10}{'是' if err.retryable else '否':<8}"
            f"{retry_after if retry_after is not None else '-'}"
        )
    print()
    print("读法：左边是上游给我们的码，右边是我们给客户端的码。两列不一样的每一行，")
    print("      都是边界层在做翻译——尤其是 401（上游说'你的 key 错了'，我们说'我们自己的问题'）。")
