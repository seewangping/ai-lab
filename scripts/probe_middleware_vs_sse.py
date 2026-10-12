"""对照实验：中间件写法到底会不会把 SSE 流式弄坏？

## 为什么要做这个实验，而不是凭印象下结论

`@app.middleware("http")` 底层是 Starlette 的 `BaseHTTPMiddleware`。它为了让你能
"在调用下游之前和之后都拿到 response 对象"，必须把下游的响应包起来——对一个流式
响应来说，这层包装【有可能】引入缓冲或额外的调度开销。

"有可能"三个字是重点：这件事在 Starlette 的历史上反复变过，各版本行为不同。
所以本项目不采用"教科书上说 BaseHTTPMiddleware 对流式不友好"这类说法，
而是**造三个只有中间件写法不同的应用，用真实 HTTP 各打一遍**：

    ① 完全没有中间件        ← 基线：证明测量本身没有引入假象
    ② 纯 ASGI 中间件        ← 本项目采用（middleware.py）
    ③ @app.middleware("http")  ← 对照组（BaseHTTPMiddleware）

三个应用的 SSE 路由、响应头、延迟配置完全相同，唯一变量是中间件写法。

## 怎么判断"流式被破坏了"

只看总时长会被误导（够快就行？）。真正说明问题的是**帧间隔的分布**：

  - 真流式：60 个数据帧均匀摊开，帧间隔 ≈ 配置的 delay，最大间隔也很小
  - 被缓冲：前 N 帧间隔≈0（攒着一起到），或者 TTFT ≈ 总时长
    （即"等到全部生成完才一次性收到"）

所以下面同时看 TTFT、总时长、帧数、**最大帧间隔**四项。

## 跑法

    uv run python scripts/probe_middleware_vs_sse.py

自起服务、自收尾，可反复重跑（不需要另开窗口）。
"""

import threading
import time
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from ai_lab.config import Settings
from ai_lab.middleware import RequestContextMiddleware
from ai_lab.schemas import ChatRequest
from ai_lab.streaming import SSE_HEADERS, SSE_MEDIA_TYPE, sse_chat_stream

# 固定的回复内容与延迟：三个应用必须完全一致，否则测的就不是中间件了
REPLY = "糖尿病酮症酸中毒的补液原则是首先快速补充等渗液体以恢复有效循环血量" * 2
DELAY_MS = 50.0
PORTS = {"无中间件": 8021, "纯 ASGI 中间件": 8022, "@app.middleware('http')": 8023}


def _settings() -> Settings:
    # 两把钥匙给不同值——相同会被 config.py 的校验拒绝启动
    return Settings(
        _env_file=None,
        client_api_key="probe-client",
        llm_api_key="sk-probe-upstream",
        stream_delay_ms=DELAY_MS,
    )


def build_app(middleware_style: str) -> FastAPI:
    """造一个应用。middleware_style 是唯一的变量。"""
    app = FastAPI()

    if middleware_style == "纯 ASGI 中间件":
        app.add_middleware(RequestContextMiddleware)
    elif middleware_style.startswith("@app.middleware"):
        # 这就是 BaseHTTPMiddleware 的写法：装饰器 + call_next
        @app.middleware("http")
        async def timing(request: Request, call_next: Any) -> Response:
            started = time.perf_counter()
            response = await call_next(request)
            response.headers["X-Duration-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
            return response

    @app.post("/stream")
    def stream() -> StreamingResponse:
        payload = ChatRequest(message="补液原则是什么？", session_id="probe")
        return StreamingResponse(
            # 固定 reply，避免"回复内容随请求变化"干扰对比
            sse_chat_stream(payload, _settings(), reply=REPLY),
            media_type=SSE_MEDIA_TYPE,
            headers=SSE_HEADERS,
        )

    return app


class RunningServer:
    """后台线程里跑一个 uvicorn，用法和 probe_real_vs_fake_streaming.py 一致。"""

    def __init__(self, app: FastAPI, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "RunningServer":
        self._thread.start()
        deadline = time.time() + 20
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("服务未能在 20s 内启动")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def measure(port: int) -> dict[str, Any]:
    """打真实 HTTP，把每一帧的到达时刻连同事件名一起记下来。

    为什么要连事件名一起记：如果只统计"所有数据帧之间的间隔"，
    最小值会总是 ~0.3ms —— 那是 meta/done 这两个控制帧恰好紧挨着 token 造成的，
    不是 token 的节奏。**只统计 token → token 的间隔**才能反映真实的出字节奏，
    也才能看出"到底是逐字到达，还是攒成一批一起到"。
    """
    url = f"http://127.0.0.1:{port}/stream"
    frames: list[tuple[str, float]] = []
    current_event: str | None = None
    accel_buffering: str | None = None

    started = time.perf_counter()
    with httpx.Client(timeout=60.0) as client:
        with client.stream("POST", url, json={}) as resp:
            accel_buffering = resp.headers.get("x-accel-buffering")
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    current_event = line.removeprefix("event: ")
                elif line.startswith("data:"):
                    frames.append((current_event or "?", time.perf_counter() - started))

    at = dict(frames)
    token_times = [t for name, t in frames if name == "token"]
    token_gaps = [b - a for a, b in zip(token_times, token_times[1:])]

    return {
        "frames": len(token_times),
        "all_frames": len(frames),
        "ttft": token_times[0] if token_times else -1.0,
        "total": frames[-1][1] if frames else -1.0,
        "meta_at": at.get("meta", 0.0),
        "done_at": at.get("done", 0.0),
        "max_gap": max(token_gaps) if token_gaps else -1.0,
        "min_gap": min(token_gaps) if token_gaps else -1.0,
        "accel_buffering": accel_buffering,
        "first_event": frames[0][0] if frames else "-",
        "last_event": frames[-1][0] if frames else "-",
    }


def main() -> int:
    print("=" * 78)
    print("中间件写法 vs SSE 流式：三个应用，唯一变量是中间件")
    print("=" * 78)
    print(f"  固定回复长度 = {len(REPLY)} 字符   配置帧间隔 = {DELAY_MS:.0f}ms")
    print("  （注意：Windows 上 asyncio.sleep 会向上取整到 15.6ms 的整数倍，")
    print("    所以实测间隔会大于配置值。这一点在三个应用里机会均等，不影响对比有效性。）")
    print()

    results: dict[str, dict[str, Any]] = {}
    for style, port in PORTS.items():
        with RunningServer(build_app(style), port):
            # ⚠️ 先打一发【预热】，结果丢掉。
            #
            # 第一版没有这一步，结果第一个被测的应用（正好是"无中间件"基线）
            # 报出 TTFT 0.905s —— 而它是唯一"什么都没加"的那个。
            # 按字面读会得出"加了中间件反而快了 0.8 秒"的荒谬结论。
            # 真实原因是冷启动：进程刚起来、事件循环刚建、httpx 连接池还是空的，
            # 这些都算进了第一个被测对象的账上。
            # **测量顺序不该改变结论** —— 所以每条真实测量前先跑一发热身。
            measure(port)
            results[style] = measure(port)

    header = (
        f"{'中间件写法':<26}{'token 帧':>9}{'总帧数':>8}{'首字(TTFT)':>12}"
        f"{'末帧':>10}{'token 最大间隔':>16}{'token 最小间隔':>16}"
    )
    print(header)
    print("-" * len(header))
    for style, r in results.items():
        print(
            f"{style:<26}{r['frames']:>9}{r['all_frames']:>8}{r['ttft']:>11.3f}s"
            f"{r['total']:>9.3f}s{r['max_gap'] * 1000:>14.1f}ms{r['min_gap'] * 1000:>14.1f}ms"
        )
    print()
    print("  注：'token 帧' 只数 event: token；'总帧数' 还含 meta / done / 心跳注释。")
    print("      'token 间隔' 只统计相邻两个 token 之间，不含控制帧——")
    print("      否则 meta 紧挨第一个 token 会造出一个 ~0ms 的假最小值。")
    print()

    baseline = results["无中间件"]
    pure = results["纯 ASGI 中间件"]
    base_http = results["@app.middleware('http')"]

    print("=" * 78)
    print("怎么读这张表")
    print("=" * 78)
    print()
    print("① TTFT 远小于总时长 ⇒ 流式是真的（不是「等全部生成完再给」）")
    for style, r in results.items():
        verdict = "是" if r["ttft"] < r["total"] * 0.5 else "否 ← 疑似被整体缓冲"
        print(f"     {style:<26} TTFT {r['ttft']:.3f}s / 总 {r['total']:.3f}s → {verdict}")
    print()
    print("② 帧间隔稳定 ⇒ 每帧都真的单独到达，没有被攒起来凑成一批")
    for style, r in results.items():
        spread = r["max_gap"] - r["min_gap"]
        verdict = "稳定" if spread < 0.05 else f"波动 {spread * 1000:.0f}ms"
        print(f"     {style:<26} {r['min_gap'] * 1000:.1f}~{r['max_gap'] * 1000:.1f}ms → {verdict}")
    print()

    print("=" * 78)
    print("结论")
    print("=" * 78)
    same_frames = pure["frames"] == base_http["frames"] == baseline["frames"]
    print(f"  三者帧数一致（{baseline['frames']}）: {same_frames}")
    delta_ttft = (base_http["ttft"] - pure["ttft"]) * 1000
    delta_total = (base_http["total"] - pure["total"]) * 1000
    print(f"  BaseHTTPMiddleware 相对纯 ASGI：TTFT {delta_ttft:+.1f}ms，总时长 {delta_total:+.1f}ms")
    print()

    if abs(delta_ttft) < 50 and abs(delta_total) < 100:
        print("  → 在本机、本 Starlette 版本（1.7.0）下，BaseHTTPMiddleware **没有**造成")
        print("     可观测的流式退化。也就是说：「教科书上说它对流式不友好」这个说法，")
        print("     在这个版本上不成立——它当年的问题已经被修掉了。")
        print()
        print("     那为什么本项目仍然用纯 ASGI？理由不是「性能」，而是「职责清晰」：")
        print("       · BaseHTTPMiddleware 会额外包一层，改变异常传播与部分响应头的行为")
        print("       · 纯 ASGI 只是转手消息，没有「包一层」这个概念，排查时序问题时少一个变量")
        print("       · 代价很小：就是拿不到漂亮的 Request/Response 对象")
        print("     一句话：选纯 ASGI 是为了少一层不可见的抽象，不是为了跑得更快。")
    else:
        print("  → 出现可观测差异，说明在当前版本下 BaseHTTPMiddleware 确实影响了流式。")
        print("     本项目使用纯 ASGI 中间件，正是为了避开这一点。")
    print()
    print("  ⚠️ 这个结论有明确的适用边界：本机 + Starlette 1.7.0 + 这个量级的响应。")
    print("     「本地测不出来」从来不等于「线上没问题」——这与 Day 4 的")
    print("     X-Accel-Buffering（本地好、线上坏）是同一类提醒。")
    print()

    for style, r in results.items():
        print(f"  {style:<26} X-Accel-Buffering = {r['accel_buffering']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
