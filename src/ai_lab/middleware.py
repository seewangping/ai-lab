"""请求上下文中间件：每个请求一行 JSON 访问日志 + 一个可追踪的 request_id。

## 为什么必须用"纯 ASGI"写法，而不是 `@app.middleware("http")`

这是 Day 6 里唯一一处「写错了照样能跑，但会把 Day 4/5 的成果悄悄毁掉」的地方。

`@app.middleware("http")` 底层是 Starlette 的 `BaseHTTPMiddleware`。它为了让你能
"在调用下游之前和之后都能拿到 response 对象"，必须把下游的响应包起来——对一个
流式响应来说，这层包装意味着缓冲与额外的任务调度，SSE 的"一个 token 一个 token
到达"可能退化成"等全部生成完再一次性到达"，而且**在本地用 curl 完全看不出来**
（小响应、本地环回，缓冲窗口根本填不满）。

纯 ASGI 中间件不做任何包装：它只是站在中间转手事件——

    http.request  ↓ 进来
    http.response.start  ↑ 出去（顺手把 request_id 塞进响应头）
    http.response.body   ↑ 出去（可能有多条，一条 token 就是一条）

不包装 ⇒ 不缓冲 ⇒ 流式还是流式。代价是拿不到漂亮的 Request/Response 对象，
只能直接看 ASGI 消息字典。**这点不方便正是它不作弊的原因。**

具体差异我没有只靠推理，而是写了个对照实验去量：
    scripts/probe_middleware_vs_sse.py
结论与实测数据见该脚本输出与 README。

## 为什么中间件放这里，异常处理器放 handlers.py

两者都在"边界层"，但职责不同：
  中间件管**所有请求**（包括成功的那些），产出的是观测数据（日志）。
  异常处理器管**出错的请求**，产出的是响应体。
把观测和响应分开，是为了能单独改一个而不碰另一个。
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ai_lab.logging_setup import get_logger

log = get_logger(__name__)

# ASGI 的三种消息字典类型（简化版：我们只关心这几个键）
Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# 响应头名。回写它是为了"用户截图报错"时我们能凭这个 ID 直接 grep 到那一条日志。
REQUEST_ID_HEADER = "X-Request-ID"

# request_id 取 hex 前 12 位：48 bit 的随机量，单机上碰撞概率可以忽略，
# 但比完整的 32 位短一半——这种东西会被贴进 issue、写进工单，短一点更好抄。
REQUEST_ID_LENGTH = 12


def new_request_id() -> str:
    return uuid.uuid4().hex[:REQUEST_ID_LENGTH]


class RequestContextMiddleware:
    """纯 ASGI 中间件：生成 request_id、写访问日志、回写响应头。

    它同时把 request_id 放进 `scope["state"]`，这样下游的路由和异常处理器
    都能用 `request.state.request_id` 取到同一个值——**这是错误响应体里那个
    request_id 能与访问日志对上的原因**。没有这一步，客户端拿到的 id 就是一张
    对不上号的空头支票。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 只处理 HTTP。WebSocket 与 lifespan 直接放行——注意这不是"忘了"。
        # WS 一次连接只对应一条升级请求，"一请求一行日志"的模型套上去会得出
        # 误导性统计：一条活了 10 分钟的连接，耗时该记 0.001s（握手）还是 600s？
        # 想观测 WS 得单独设计指标（连接数、存活时长、消息数），见 ws.py 的 ConnectionManager。
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        # 下游（路由、异常处理器）都在同一个 scope 上工作，state 是它们共享的通道。
        # setdefault 是为了兼容"上游已经建好 state"的情况。
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        method = str(scope.get("method", "-"))
        path = str(scope.get("path", "-"))
        started = time.perf_counter()

        # 默认 500：万一下游一个 http.response.start 都没发就崩了，
        # 日志里也得有个状态码，而不是空白。
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_started = True
                # 复制一份再改，不原地改上游递过来的字典——它可能还被别人引用着
                headers = list(message.get("headers") or [])
                already_set = any(
                    key.lower() == REQUEST_ID_HEADER.lower().encode("latin-1")
                    for key, _ in headers
                )
                # 已经有就不加：错误处理器也会写这个头（因为未处理异常的 500
                # 不经过本中间件，见 handlers.py 的说明），重复添加会得到两个同名头。
                if not already_set:
                    headers.append(
                        (
                            REQUEST_ID_HEADER.lower().encode("latin-1"),
                            request_id.encode("latin-1"),
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        # 记录下游是否抛了异常，好在 finally 里把它一起写进那【一行】日志。
        caught: BaseException | None = None
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as exc:
            # 理论上异常会在更内层的异常处理器里被接住（见 handlers.py）。
            # 冒到这里说明"连异常处理器自己都出问题了"。这里只记下它、继续往外抛，
            # 交给最外层的 ServerErrorMiddleware —— 它是框架保证一定存在的那一层。
            #
            # 为什么不在这个 except 里直接打日志：那样一次失败会打出【两行】
            # （一行带栈、一行访问日志），而"每个请求一行"是这套设计的前提——
            # 多一行意味着日志量翻倍、统计口径被污染。改成把栈挂到访问日志那一行上。
            caught = exc
            raise
        finally:
            # 用 finally 而不是写在成功分支里：**失败的请求更需要被记录**。
            # 只在成功路径打日志，是"线上出问题时日志里什么都没有"的经典成因。
            level = logging.ERROR if caught is not None else (
                logging.INFO if status_code < 500 else logging.ERROR
            )
            log.log(
                level,
                "请求异常退出" if caught is not None else "请求完成",
                # exc_info 接受异常实例（logging 会自己转成三元组），
                # JsonFormatter 会把它渲染成 exc 字段。同一个 request_id 既能查到
                # "哪个接口慢了"，也能查到"那次为什么炸了"。
                exc_info=caught,
                extra=self._log_fields(request_id, method, path, status_code, started),
            )

    @staticmethod
    def _log_fields(
        request_id: str, method: str, path: str, status_code: int, started: float
    ) -> dict[str, object]:
        """访问日志的字段。

        这些键名（method/path/http_status/duration_ms）刻意不叫 message/level 之类——
        logging 的 extra 不许与 LogRecord 自带属性重名，重名会直接抛 KeyError。
        顺带说：这也是"结构化日志"的价值所在，它们是可被查询的独立字段，
        不是塞在消息文本里等着被正则捞出来。
        """
        return {
            "request_id": request_id,
            "method": method,
            "path": path,
            "http_status": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
