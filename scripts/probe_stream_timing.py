"""对比探针：同一条流式接口，TestClient（进程内）vs 真实 HTTP，首帧到达时间差多少。

为什么值得单独写个脚本：
  写测试时发现「用 TestClient 读流，首帧要等 1.4 秒才到」——看起来像代码被缓冲了。
  实际是 TestClient 的 ASGI 传输层把整个响应体收完才交给 httpx，帧内容全对、
  顺序也全对，**只有时间维度是假的**。

  这条差异有直接的现实投影：真实世界里缓冲流的东西是 Nginx、云负载均衡、
  企业代理——它们同样是"内容没错、时序全错"。所以：
     流式的【内容/顺序】可以用 TestClient 断言；
     流式的【时序】必须打真实 HTTP 才算数。
  本脚本就是那个"打真实 HTTP"的计时器。

用法：
  cd /d/ai-lab
  uv run python scripts/probe_stream_timing.py
"""

import sys
import time
from collections.abc import Iterable

import httpx
from fastapi.testclient import TestClient

from ai_lab.api import app
from ai_lab.config import get_settings

BASE_URL = "http://127.0.0.1:8000"
PAYLOAD = {"message": "糖尿病酮症酸中毒的补液原则是什么？", "session_id": "sess-probe"}


def _fmt(value: float | None) -> str:
    return "未收到" if value is None else f"{value:.3f}s"


def measure(lines: Iterable[str], label: str) -> None:
    """逐行读取并记录：首帧时刻、末帧时刻、data 帧数量。"""
    started = time.perf_counter()
    first_at: float | None = None
    last_at: float | None = None
    data_frames = 0

    for line in lines:
        if not line.startswith("data:"):
            continue
        now = time.perf_counter() - started
        if first_at is None:
            first_at = now
        last_at = now
        data_frames += 1

    gap = (last_at or 0) - (first_at or 0)
    print(f"[{label}]")
    print(f"    首帧到达 : {_fmt(first_at)}")
    print(f"    末帧到达 : {_fmt(last_at)}")
    print(f"    首末间隔 : {_fmt(gap)}  ← 这个值接近 0 就说明被缓冲了")
    print(f"    data 帧数: {data_frames}")
    print()


def main() -> int:
    settings = get_settings()
    auth = {"X-API-Key": settings.llm_api_key}
    print(f"token 间隔配置: {settings.stream_delay_ms} ms")
    print("-" * 70)

    # A) 进程内：TestClient
    client = TestClient(app)
    with client.stream("POST", "/chat/stream", json=PAYLOAD, headers=auth) as resp:
        measure(resp.iter_lines(), "TestClient（进程内 ASGI）")

    # B) 真实 HTTP：需要服务已在 127.0.0.1:8000 运行
    try:
        with httpx.Client(base_url=BASE_URL, timeout=30.0) as http:
            with http.stream("POST", "/chat/stream", json=PAYLOAD, headers=auth) as resp:
                if resp.status_code != 200:
                    print(f"[真实 HTTP] 服务返回 {resp.status_code}，跳过")
                    return 1
                print(f"[真实 HTTP] content-type={resp.headers.get('content-type')}")
                print(f"            x-accel-buffering={resp.headers.get('x-accel-buffering')}")
                measure(resp.iter_lines(), "真实 HTTP（127.0.0.1:8000）")
    except httpx.ConnectError:
        print(f"[真实 HTTP] 连不上 {BASE_URL}，请先启动服务：")
        print("    uv run uvicorn ai_lab.api:app --reload --reload-dir src")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
