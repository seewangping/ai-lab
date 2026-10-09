"""FastAPI 应用入口：W2 的 HTTP 服务从最小可运行开始。

对照 C#：路由装饰器 ≈ ASP.NET Core 的 [HttpGet]，
但没有 Controller 类——函数即端点；配置等依赖用 Depends 注入（Day 3 再接）。
"""

import time

from fastapi import FastAPI, Path, Query, Request
from fastapi.responses import StreamingResponse

from ai_lab.deps import ApiKeyDep, SettingsDep
from ai_lab.schemas import (
    SESSION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
)
from ai_lab.stub_llm import MODEL_NAME, build_reply
from ai_lab.streaming import SSE_HEADERS, SSE_MEDIA_TYPE, sse_chat_stream
from ai_lab.ws import router as ws_router

app = FastAPI(
    title="ai-lab API",
    description="W2 练习：从最小服务长成支持流式输出的对话服务（W4 目标）。",
    version="0.3.0",
)

# 路由拆分：WS 那部分单独放在 ws.py，用 include_router 挂进来。
# 好处是这个文件不会随着功能增加无限膨胀，而且 ws.py 能独立被测试。
# 对照 C#：等同于把 Controller 拆成多个文件后统一注册进 ApplicationBuilder。
app.include_router(ws_router)


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
