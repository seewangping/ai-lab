"""Day 4 实验：串行 vs 并行、超时控制、并发限制。"""
import asyncio
import time
import httpx

_DELAY = 3.0          # 模拟一次 LLM 调用的耗时（秒）
_CONCURRENCY = 3      # 并发上限
url1 = "https://httpbin.org/delay/1"
url2 = "https://httpbingo.org/delay/1"
url3 = "https://www.baidu.com"

# Python 3.10+ 的 Semaphore 创建时不绑定事件循环，模块级创建即可，
# 不需要 C# 式的懒加载单例（lazy getter）。
_SEM = asyncio.Semaphore(_CONCURRENCY)


async def fake_llm_call(n: int) -> str:
    """模拟一次 LLM 调用：等待 _DELAY 秒后返回结果。"""
    async with _SEM:
        await asyncio.sleep(_DELAY)
        return f"result-{n}"


async def call_with_timeout(n: int, timeout: float) -> str | None:
    """带超时的调用，超时返回 None。注意返回类型要写 str | None。"""
    try:
        return await asyncio.wait_for(fake_llm_call(n), timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def run_timeout_demo() -> None:
    """超时演示：注意 wait_for 的超时从「调用发起」起算，包含排队等信号量的时间。"""
    start = time.perf_counter()
    timeouts = [2.0, 2.0, 2.0, 5.1, 2.0]
    results = await asyncio.gather(
        *(call_with_timeout(i, t) for i, t in enumerate(timeouts))
    )
    print(f"[超时]   耗时 {time.perf_counter() - start:.2f}s  {results}\n")


async def run_parallel() -> None:
    """并行演示：5 个调用在并发上限 3 的限制下分两批跑完。"""
    start = time.perf_counter()
    results = await asyncio.gather(*(fake_llm_call(i) for i in range(5)))
    print(f"[并行]   耗时 {time.perf_counter() - start:.2f}s  {results}\n")

async def run_http_demo() -> None:
    """HTTP 请求演示：5 个真实请求同时发出，观察真实网络的并行收益。"""
    start = time.perf_counter()
    # timeout=30：httpx 默认超时只有 5s，5 个并发 TLS 握手挤在一起容易被误杀。
    # 生产中这个值要按上游 SLA 定（LLM 网关一般给 60~120s）。
    async with httpx.AsyncClient(timeout=30.0) as client:
        responses = await asyncio.gather(
            *(client.get(url1) for _ in range(5))
        )
        results = [f"{r.status_code} {r.elapsed.total_seconds():.2f}s" for r in responses]

    print(f"[HTTP]   耗时 {time.perf_counter() - start:.2f}s  {results}\n")

async def run_http_serial() -> None:
    """HTTP 请求演示：5 个真实请求一个接一个发出，观察真实网络的串行收益。"""
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(5):
            r = await client.get(url1)
            print(f"[HTTP串行]   第 {i} 个: {r.status_code} {r.elapsed.total_seconds():.2f}s")
        print(f"[HTTP串行]   耗时 {time.perf_counter() - start:.2f}s\n")


async def run_serial() -> None:
    """串行演示：一个接一个，总共 5 x 3 = 15 秒。"""
    start = time.perf_counter()
    for i in range(5):
        result = await fake_llm_call(i)
        print(f"[串行]   第 {i} 个: {result}")
    print(f"[串行]   耗时 {time.perf_counter() - start:.2f}s\n")


async def main() -> None:
    await run_http_demo()
    await run_http_serial()
    #await run_timeout_demo()
    #await run_parallel()
    #await run_serial()


if __name__ == "__main__":
    asyncio.run(main())
