"""FastAPI 应用入口：W2 的 HTTP 服务从最小可运行开始。

对照 C#：路由装饰器 ≈ ASP.NET Core 的 [HttpGet]，
但没有 Controller 类——函数即端点；配置等依赖用 Depends 注入（Day 3 再接）。
"""

import time

from fastapi import FastAPI, Path, Query

from ai_lab.schemas import (
    SESSION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
)

app = FastAPI(
    title="ai-lab API",
    description="W2 练习：从最小服务长成支持流式输出的对话服务（W4 目标）。",
    version="0.2.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """存活探针。容器编排的 liveness / readiness 都会打这里，永远返回 200。"""
    return {"status": "ok"}


@app.get("/greet/{name}")
def greet(name: str, excited: bool = False) -> dict[str, str]:
    """最小示例：路径参数 + 布尔查询参数，/docs 里可直接调试。"""
    message = f"Hello, {name}!" + ("!" if excited else "")
    return {"message": message}


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> dict[str, object]:
    """对话端点（Day 2 版本：接住并回显，W3 换成真实 LLM 调用）。

    请求体不是手写解析的：ChatRequest 一声明，FastAPI 就负责
      - 解析 JSON（Content-Type 不对、JSON 语法错 → 422）
      - 逐字段校验（缺字段、类型错、min_length/pattern/ge 不满足 → 422，附字段路径）
      - 把干净的 ChatRequest 实例交给你
    """
    started = time.perf_counter()

    reply = (
        f"[stub] 收到 {len(payload.message)} 字，session={payload.session_id}，"
        f"temperature={payload.temperature}，stream={payload.stream}"
    )

    # 故意多返回两个没在 ChatResponse 里声明的键，用来验证 response_model 会过滤掉它们。
    # 真实场景对应"不小心把内部对象整体 return 出去"——过滤是最后一道防线，不是唯一一道。
    return {
        "reply": reply,
        "session_id": payload.session_id,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "_internal_trace_id": "trace-should-be-filtered",
        "_raw_prompt": "内部提示词，绝不能出现在响应里",
    }


@app.get("/chat/history/{session_id}")
def chat_history(
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
