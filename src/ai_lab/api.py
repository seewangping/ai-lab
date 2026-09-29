"""FastAPI 应用入口：W2 的 HTTP 服务从最小可运行开始。

对照 C#：路由装饰器 ≈ ASP.NET Core 的 [HttpGet]，
但没有 Controller 类——函数即端点；配置等依赖用 Depends 注入（Day 3 再接）。
"""

from fastapi import FastAPI

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
