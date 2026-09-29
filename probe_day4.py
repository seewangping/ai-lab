"""Day 4 知识点验证：wait_for 到底返回什么、gather 遇到异常怎么办。"""
import asyncio
import time


async def slow(n: int, sec: float) -> str:
    await asyncio.sleep(sec)
    return f"ok-{n}"


async def boom(n: int) -> str:
    await asyncio.sleep(0.5)
    raise ValueError(f"任务 {n} 炸了")


async def q1_wait_for_returns_what() -> None:
    """问题：wait_for 超时后是「返回 None」还是「抛异常」？"""
    print("=== Q1: wait_for 超时后发生了什么 ===")
    try:
        r = await asyncio.wait_for(slow(1, 3.0), timeout=1.0)
        print(f"  wait_for 返回值 = {r}")
    except asyncio.TimeoutError:
        print("  wait_for 抛出了 TimeoutError（不是返回 None）")
    except asyncio.CancelledError:
        print("  wait_for 抛出了 CancelledError")

    # 我们代码里的 return None 是自己写的，不是 wait_for 的返回值
    async def with_our_wrapper() -> str | None:
        try:
            return await asyncio.wait_for(slow(2, 3.0), timeout=1.0)
        except asyncio.TimeoutError:
            return None  # <- None 是这一行给出的

    print(f"  我们自己包装后 = {await with_our_wrapper()}（None 来自 except 里的 return）\n")


async def q2_timeout_cancels_inner() -> None:
    """超时后内部协程是被取消了，还是继续跑？"""
    print("=== Q2: 超时后内部协程的命运 ===")
    finished = False

    async def side_effect() -> None:
        nonlocal finished
        await asyncio.sleep(2.0)
        finished = True
        print("  内部协程跑完了（说明没被取消）")

    try:
        await asyncio.wait_for(side_effect(), timeout=0.5)
    except asyncio.TimeoutError:
        print("  超时了")

    await asyncio.sleep(2.0)  # 再等一会儿看它会不会继续
    print(f"  2 秒后 finished = {finished}（False = 内部协程被取消了）\n")


async def q3_gather_exception() -> None:
    """gather 里某个任务抛异常，其他任务会怎样？"""
    print("=== Q3: gather 遇到异常 ===")
    # 默认：第一个异常立刻往上抛，但其他任务不会停
    try:
        await asyncio.gather(slow(1, 1.0), boom(2), slow(3, 1.0))
    except ValueError as e:
        print(f"  默认行为：gather 抛出 {e!r}，拿不到任何结果（包括成功的）")

    # return_exceptions=True：异常当结果返回
    results = await asyncio.gather(
        slow(1, 1.0), boom(2), slow(3, 1.0), return_exceptions=True
    )
    print(f"  return_exceptions=True：{results}")


async def main() -> None:
    t0 = time.perf_counter()
    await q1_wait_for_returns_what()
    await q2_timeout_cancels_inner()
    await q3_gather_exception()
    print(f"整个探针耗时 {time.perf_counter() - t0:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
