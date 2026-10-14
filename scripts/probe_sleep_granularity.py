"""测量 `asyncio.sleep` 与 `time.sleep` 的实际睡眠精度。

## 为什么需要这个脚本

README 和 `docs/sse-streaming-pitfalls.md` 里有一条结论：

    Windows 上 asyncio.sleep 会被向上取整到约 15.6ms 的整数倍，
    而 time.sleep 是准的。所以本机 STREAM_DELAY_MS=20 实际节奏约 31ms/token。

这条结论最初是靠临时脚本量出来的，用完就丢了。**没有复现手段的结论等于传闻**——
所以补上这个脚本，让它可被任何人在任何机器上重新验证。

## 它验证什么

1. **不变量（所有平台都成立）**：`asyncio.sleep(n)` 与 `time.sleep(n)` 都不会
   明显早于 n 返回。这是它们唯一的硬保证——「至少睡够」，不是「恰好睡够」。
   这条会被断言，因此脚本可以直接进 CI。

2. **平台现象（只在 Windows 成立）**：实际耗时被量化到某个固定粒度上。
   脚本会**测出**那个粒度（而不是硬编码 15.6ms），再检查实测值是否符合
   `ceil(n / G) * G`——如果符合，说明确实是「按粒度向上取整」而不是「加了固定开销」。
   这一条只报告，不断言：Linux 上没有这个现象，断言它会让 CI 在 Linux 上无故变红。

用法：
    uv run python scripts/probe_sleep_granularity.py

退出码：不变量成立 -> 0，否则 -> 1。
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from statistics import median

# 测量点。1ms 这个点不是随便选的：如果存在粒度 G，那么请求 1ms 时
# 实际会睡到下一个刻度 ≈ G 本身，所以它直接给出粒度的量级。
DURATIONS_MS: tuple[float, ...] = (1.0, 5.0, 20.0, 50.0, 100.0)

# 每档重复次数。取中位数而不是平均：偶发的调度抖动只会拉长单次结果，
# 用平均会被少数离群值带偏。
ROUNDS = 9

# 判定「没有早返回」的容差。允许 2% 的测量噪声——perf_counter 与调度
# 之间总有微小偏差，但不可能真的提前睡醒。
TOLERANCE = 0.98


async def measure_asyncio(ms: float, rounds: int) -> list[float]:
    """逐次测量 asyncio.sleep 的实际耗时（毫秒）。"""
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        await asyncio.sleep(ms / 1000)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def measure_blocking(ms: float, rounds: int) -> list[float]:
    """逐次测量 time.sleep 的实际耗时（毫秒）。"""
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        time.sleep(ms / 1000)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def fmt(samples: list[float]) -> str:
    return f"中位 {median(samples):7.1f}  最小 {min(samples):7.1f}  最大 {max(samples):7.1f}"


def fit_quantum(medians: dict[float, float]) -> tuple[float, float]:
    """网格搜索最贴合实测数据的粒度 G，模型为 `实测 ≈ ceil(请求 / G) * G`。

    ## 为什么不直接用「请求 1ms 时实测到的那个值」当 G

    因为单点估计带观测误差，而 `ceil` 是**阶跃函数**——G 只要稍微偏小，
    在较大的请求点上就会被放大成整整一个步长。实测过这一次：

        用最小请求点反推 G=15.3 时
        请求 20ms  -> 预测 30.6，实测 31.7（差 1.1）
        请求 50ms  -> 预测 61.2，实测 63.0（差 1.8）
        请求 100ms -> 预测 107.1，实测 109.0（差 1.9）

    偏差随请求时长累积，把本来拟合得很好的模型判成了「不符合」。
    改成在全域上拟合并用**最小化最大偏差**（而不是最小二乘）——
    阶跃模型下 minimax 更合适，而且它返回的残差正好就是我要报告的判定依据。

    返回 (最优粒度, 该粒度下的最大偏差)，单位均为毫秒。
    """
    best_g = 1.0
    best_err = float("inf")
    for hundredths in range(1000, 2001):  # 10.00ms ~ 20.00ms，步长 0.01ms
        candidate = hundredths / 100
        err = max(
            abs(medians[ms] - math.ceil(ms / candidate) * candidate) for ms in medians
        )
        if err < best_err:
            best_err = err
            best_g = candidate
    return best_g, best_err


def main() -> int:
    print(f"Python {sys.version.split()[0]} on {sys.platform}，每档重复 {ROUNDS} 次\n")

    print("=" * 74)
    print("asyncio.sleep")
    print("=" * 74)
    aio_medians: dict[float, float] = {}
    for ms in DURATIONS_MS:
        samples = asyncio.run(measure_asyncio(ms, ROUNDS))
        aio_medians[ms] = median(samples)
        ratio = median(samples) / ms
        print(f"  请求 {ms:6.1f}ms -> {fmt(samples)}   倍数 {ratio:5.2f}x")

    print("\n" + "=" * 74)
    print("time.sleep（对照）")
    print("=" * 74)
    block_medians: dict[float, float] = {}
    for ms in DURATIONS_MS:
        samples = measure_blocking(ms, ROUNDS)
        block_medians[ms] = median(samples)
        ratio = median(samples) / ms
        print(f"  请求 {ms:6.1f}ms -> {fmt(samples)}   倍数 {ratio:5.2f}x")

    # ---------------- 不变量：两者都不会明显早于请求时长返回 ----------------
    print("\n" + "=" * 74)
    print("不变量：sleep 的硬保证是「至少睡够」，不是「恰好睡够」")
    print("=" * 74)
    failures: list[str] = []
    for ms in DURATIONS_MS:
        for label, measured in (("asyncio", aio_medians[ms]), ("time", block_medians[ms])):
            if measured < ms * TOLERANCE:
                failures.append(f"{label}.sleep({ms}ms) 中位数只有 {measured:.1f}ms —— 提前返回了")
            else:
                print(f"  ✓ {label}.sleep({ms:6.1f}ms) 实际 {measured:7.1f}ms 未提前返回")
    if failures:
        print("\n✗ 断言失败：")
        for item in failures:
            print(f"    {item}")
        return 1

    # ---------------- 现象：是否被量化到某个固定粒度 ----------------
    print("\n" + "=" * 74)
    print("平台现象：实际耗时是否被量化到固定粒度")
    print("=" * 74)

    quantum, residual = fit_quantum(aio_medians)
    print(f"  拟合结果：粒度 G ≈ {quantum:.2f}ms，最大偏差 {residual:.2f}ms")
    print("  参考：Windows 的定时器刻度是 15.625ms（= 64Hz，即经典的一「tick」）\n")

    tolerance = max(1.0, quantum * 0.06)
    for ms in DURATIONS_MS:
        steps = math.ceil(ms / quantum)
        expected = steps * quantum
        actual = aio_medians[ms]
        mark = "✓" if abs(actual - expected) <= tolerance else "✗"
        print(f"  {mark} 请求 {ms:6.1f}ms -> ceil({ms:.0f}/{quantum:.1f})={steps} 步 -> 预测 {expected:6.1f}ms，实测 {actual:6.1f}ms")

    print()
    fitted = residual <= tolerance
    if fitted:
        print("  结论：asyncio.sleep 的实际耗时符合「按固定粒度向上取整」模型。")
        print(f"        本机粒度 G ≈ {quantum:.2f}ms。这解释了「配置 20ms、实测 31ms」的差值——")
        print(f"        ceil(20/{quantum:.2f})=2，2×{quantum:.2f}≈{2 * quantum:.1f}ms。两个数都是对的，")
        print("        只是当时没追究那个差值从哪来。")
    else:
        print("  结论：实测值**不**符合「按固定粒度向上取整」模型。")
        print("        通常意味着本平台没有这个量化现象（Linux 事件循环没有）。")
        print("        请勿据此下任何结论——本脚本只报告观察到的事实。")

    # 对照：同样是睡眠，time.sleep 是否也这样？这能定位到"是事件循环的问题"
    # 还是"整个操作系统都做不了精细睡眠"。
    block_residual_sum = sum(
        abs(block_medians[ms] - ms) for ms in DURATIONS_MS
    )
    aio_residual_sum = sum(
        abs(aio_medians[ms] - ms) for ms in DURATIONS_MS
    )
    print(f"\n  对照：相对请求时长的总偏差 —— asyncio {aio_residual_sum:.1f}ms vs time.sleep {block_residual_sum:.1f}ms")
    if aio_residual_sum > block_residual_sum * 2:
        print("        → time.sleep 明显更准。所以这是【事件循环定时器】的问题，")
        print("          不是操作系统整体做不了精细睡眠。（Windows 上 time.sleep 走 waitable timer）")
    else:
        print("        → 两者精度接近，本平台未观察到事件循环定时器粒度问题。")

    print("\n" + "=" * 74)
    print("判定：不变量成立（退出码 0）。平台现象只报告、不断言——")
    print("      它只在 Windows 上出现，在 Linux 上断言它会让 CI 无故变红。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
