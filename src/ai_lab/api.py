"""FastAPI 应用入口：W2 的 HTTP 服务从最小可运行开始。

对照 C#：路由装饰器 ≈ ASP.NET Core 的 [HttpGet]，
但没有 Controller 类——函数即端点；配置等依赖用 Depends 注入（Day 3 再接）。
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Path, Query, Request
from fastapi.responses import StreamingResponse

from ai_lab.demo import router as demo_router
from ai_lab.deps import ApiKeyDep, SettingsDep
from ai_lab.handlers import register_exception_handlers
from ai_lab.logging_setup import setup_logging
from ai_lab.middleware import RequestContextMiddleware
from ai_lab.schemas import (
    SESSION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
)
from ai_lab.stub_llm import MODEL_NAME, build_reply
from ai_lab.streaming import SSE_HEADERS, SSE_MEDIA_TYPE, sse_chat_stream
from ai_lab.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动时做的事。目前只有一件：装好结构化日志。

    为什么必须放在这里，而不是模块顶层直接调 setup_logging()：

    ① **避免导入副作用**。模块顶层写副作用意味着"import 这个模块"和
       "运行这个服务"变成同一件事——测试里 import 一下就改了全局日志配置，
       别人再也无法局部地使用这个模块。（这是 Python 与 C# 的一个显著差异：
       C# 里 import 只加载类型，不会执行配置代码。）

    ② **时机要在框架配置日志之后**。uvicorn 启动时会先用 dictConfig 配置自己的
       logger（uvicorn / uvicorn.error / uvicorn.access）。我们的配置如果发生在
       import 阶段，可能早于它而被覆盖；放在 lifespan 里就稳定晚于它。

    ③ 这是**唯一**能让"每个请求一行结构化日志"在真实服务里成立的地方。

    ⚠️ 这一条是实测发现的，不是设计时想到的：
    最初 setup_logging() 只在 logging_setup.py 自己的 __main__ 里被调用，
    而测试用例各自显式调用它——于是测试全绿，真实跑起来却从未装过 JsonFormatter。

    后果（对照 uvicorn 0.54.0 的默认日志配置逐条观察到的）：

      - **ai_lab 包的日志全部交给 logging.lastResort**。它只在"整条继承链上一个
        handler 都没有"时生效，恰好是这种情况。lastResort 的行为是：
        只处理 WARNING 及以上、纯文本、写往 stderr。
          · 中间件的 INFO 访问日志 → **整条被丢弃**（"每个请求一行"当场不成立）
          · 400 这类 WARNING → 打出来了，但没有 JSON 外壳，纯文本，
            任何按 JSON 解析的采集器都会解析失败
      - uvicorn 自己的日志不受影响：uvicorn.error 会**向父 logger `uvicorn`
        传播**，而 `uvicorn` 自带一个 default handler，所以它的 "Exception in
        ASGI application" 一直正常输出。这也解释了为什么当时看起来
        "日志是有的"——恰恰掩盖了业务日志已经缺了一半。

    ⚠️ 这里有一个**必须说准**的细节（我第一次就写错了，因为听起来很合理）：
    uvicorn 自己的日志**不会**跟着进我们的 JSON handler。原因是 uvicorn 0.54.0
    的默认配置里 `uvicorn.propagate = False`，而 `uvicorn.error` 是它的子 logger——
    传播链在 `uvicorn` 那一层就被切断了，记录根本到不了 root。

    实测（一个请求打一次 `/demo/errors/unhandled`，stdout / stderr 分流统计）：
        stdout（我们的 JSON handler）    10 行，其中 ERROR 只有 2 条
                                        （中间件 1 + 异常处理器 1）
        stderr（uvicorn 的 default handler）  82 行，纯文本，含
                                        "ERROR:    Exception in ASGI application"
                                        + 完整 traceback
    也就是说两条流是**干净分离**的：stdout 只有我们的 JSON，stderr 只有框架自己的
    纯文本。没有重复，也没有互相污染——这是好事，因为采集端可以只管 stdout。
    代价是"一次未处理异常"在**总量**上仍会出现 3 份栈：中间件 1 份（带 duration_ms
    与 request_id 的请求视角）、异常处理器 1 份（语义视角）、uvicorn 1 份（框架兜底，
    无法关闭）。我们只能控制前两份，而两份的信息侧重不同，故都保留。

    记录在这里是因为这个陷阱很典型：**测试验证的是"某份配置是对的"，
    而服务跑的是"另一份配置"。**前者再绿也证明不了后者。
    """
    setup_logging()
    yield


app = FastAPI(
    title="ai-lab API",
    description="W2 练习：从最小服务长成支持流式输出的对话服务（W4 目标）。",
    version="0.4.0",
    lifespan=lifespan,
)

# 边界层的两件套。都必须在应用开始处理请求之前挂上。
#
# 顺序上有个容易搞错的点：异常处理器【不是】中间件，它跑在更内层
# （ServerErrorMiddleware → 用户中间件 → ExceptionMiddleware → 路由）。
# 所以中间件能观测到异常处理器产出的响应（包括 500），但反过来不行——
# 这也正是 request_id 必须由中间件放进 scope["state"]、再被处理器读出来的原因。
register_exception_handlers(app)
app.add_middleware(RequestContextMiddleware)

# 路由拆分：WS 与演示端点各自独立成文件，用 include_router 挂进来。
# 好处是这个文件不会随着功能增加无限膨胀，而且每个模块能独立被测试。
# 对照 C#：等同于把 Controller 拆成多个文件后统一注册进 ApplicationBuilder。
app.include_router(ws_router)
app.include_router(demo_router)


@app.get("/health")
def health() -> dict[str, str]:
    """存活探针。容器编排的 liveness / readiness 都会打这里，永远返回 200。"""
    return {"status": "ok"}


@app.get("/greet/{name}")
def greet(name: str, excited: bool = False) -> dict[str, str]:
    """最小示例：路径参数 + 布尔查询参数，/docs 里可直接调试。"""
    message = f"Hello, {name}!" + ("!" if excited else "")
    return {"message": message}


def _streaming_response(payload: ChatRequest, settings: SettingsDep, request: Request) -> StreamingResponse:
    """把异步生成器包成 SSE 响应。

    headers 里的 X-Accel-Buffering: no 是给 Nginx 看的——不写它，
    token 会在 Nginx 的代理缓冲区里被攒起来，用户看到的仍是"一次性到达"，
    而你在本机用 curl 测试却完全正常（因为它不经过 Nginx）。
    这个坑的典型特征就是「本地好、线上坏」。
    """
    return StreamingResponse(
        sse_chat_stream(
            payload,
            settings,
            # 传回调而不是 Request 对象：测试里可替换，见 tests/test_streaming.py
            is_disconnected=request.is_disconnected,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@app.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        200: {
            "description": "stream=false 时返回 JSON；stream=true 时此处返回 text/event-stream",
            "content": {"text/event-stream": {}},
        }
    },
)
def chat(
    payload: ChatRequest,
    api_key: ApiKeyDep,
    settings: SettingsDep,
    request: Request,
) -> dict[str, object] | StreamingResponse:
    """对话端点。一个端点两种响应形态，由请求体里的 stream 决定。

    这是 OpenAI 兼容接口的做法（`{"stream": true}`），好处是客户端换参数即可切换。
    代价是 OpenAPI 文档只能描述其中一种（上面的 responses 是手工补的说明）。

    想一条命令就看到流式效果，也可以用同义的 POST /chat/stream。

    注意返回类型是 `dict | StreamingResponse`：直接返回 Response 对象时，
    FastAPI 会跳过 response_model 的序列化与过滤（它认为你已经自己处理好了）。
    所以走流式分支时，过滤这件事得自己负责——好在流式的帧是我们逐帧构造的。
    """
    if payload.stream:
        return _streaming_response(payload, settings, request)

    started = time.perf_counter()

    reply = build_reply(payload, settings)

    # 故意多返回两个没在 ChatResponse 里声明的键，用来验证 response_model 会过滤掉它们。
    # 真实场景对应"不小心把内部对象整体 return 出去"——过滤是最后一道防线，不是唯一一道。
    return {
        "reply": reply,
        "session_id": payload.session_id,
        "model": MODEL_NAME,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "_internal_trace_id": "trace-should-be-filtered",
        "_raw_prompt": "内部提示词，绝不能出现在响应里",
    }


@app.post(
    "/chat/stream",
    responses={
        200: {
            "description": "SSE 流：event: meta → event: token × N（夹心跳注释）→ event: done",
            "content": {"text/event-stream": {}},
        }
    },
)
def chat_stream(
    payload: ChatRequest,
    api_key: ApiKeyDep,
    settings: SettingsDep,
    request: Request,
) -> StreamingResponse:
    """专用流式端点（与 /chat + stream=true 等价，只是 curl 起来更省事）。

    验收命令（-N = 禁用 curl 自己的输出缓冲，不加它你会看到"一次性全部出现"）：
        curl -N -X POST http://127.0.0.1:8000/chat/stream \\
          -H "Content-Type: application/json" -H "X-API-Key: $KEY" \\
          -d '{"message":"补液原则是什么？","session_id":"sess-001"}'
    """
    return _streaming_response(payload, settings, request)


@app.get("/chat/history/{session_id}")
def chat_history(
    api_key: ApiKeyDep,
    session_id: str = Path(
        pattern=SESSION_ID_PATTERN,
        description="会话 ID，格式不对直接 422",
        examples=["sess-20260929-001"],
    ),
    limit: int = Query(default=10, ge=1, le=100, description="每页条数，1~100"),
    offset: int = Query(default=0, ge=0, description="偏移量，>=0"),
) -> dict[str, object]:
    """路径参数与查询参数的约束示例。

    约束写在声明处（Path/Query）而不是函数体里——所以非法入参根本进不来，
    省掉了每个端点开头那一串 if 检查。
    """
    return {
        "session_id": session_id,
        "limit": limit,
        "offset": offset,
        "items": [],
    }
