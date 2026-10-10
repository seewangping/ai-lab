"""对照实验：ws.py 的"逐 token 推送"到底算不算真流式？

问题来自一句质疑：`_send_streamed_reply` 确实一条条 send_json 把回复吐出去了，
这难道不是流式吗？

答案：**传输层是真的，生成层是假的。**

看它第一步做了什么（ws.py:176）：

    reply = build_reply(payload, settings)

这个函数的返回类型是 `str`。也就是说——**在发出第一个 token 之前，整段回复
已经全部生成完毕了**。后面那些 `await asyncio.sleep(delay_s)` 只是把一段
"已经存在的字符串"慢慢放出来，跟流式的本质（边生成边发）毫无关系。

后果非常具体：**TTFT（首 token 延迟）一点都没改善。**
流式输出对用户唯一的真正价值就是 TTFT——不用等模型把整段话算完就能开始读。
而假流式下，用户要等的时间 = 模型生成完整段话的时间，一个字都省不下来。

为什么平时看不出来：占位模型是瞬间返回的（T_gen ≈ 0），于是"慢慢放出来"的
那些 sleep 单独制造出了"token 陆续到达"的观感。**这个幻觉只有在模型真的有
延迟时才会破——也就是 W3 上线的时候。**

本脚本用同一个"模型成本模型"跑三种形态，让差别变成秒数：

    模型规格（三种形态共用，否则对比无效）：
        第 k 个 token 在 THINK_S + k * PER_TOKEN_S 时刻就绪

    ① 现状 + 占位模型（今天）      → 生成耗时 ≈ 0，只看得到假的"逐字"
    ② 现状 + 真实延迟的模型        → TTFT 暴露，而且生成时间被付了两遍
    ③ 真流式形状（异步生成器）      → TTFT = 思考时间，不重复付费

跑法：python scripts/probe_real_vs_fake_streaming.py
自包含：自己起服务、自己收尾，不依赖 8000 端口那个开发服务器。
"""

from __future__ import annotations

import asyncio
import json
import statistics
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from websockets.asyncio.client import connect

from ai_lab.config import Settings, get_settings
from ai_lab.schemas import ChatRequest

# --------------------------------------------------------------------------
# 共用的"模型成本模型"
# --------------------------------------------------------------------------
THINK_S = 0.30  # 首 token 之前的思考时间（真实 LLM 通常几百毫秒起步）
PER_TOKEN_S = 0.02  # 每多生成一个 token 的耗时
REPLY_CHARS = 90  # 回复长度（本项目 stub 回复恰好 89~90 字符）

REPLY_TEXT = (
    "[stub] 这是对照实验用的固定回复文本：真实场景里这段内容由模型生成，"
    "逐字吐出的同时用户已经开始阅读了。"
) * 3
REPLY_TEXT = REPLY_TEXT[:REPLY_CHARS]

PORT_FAKE = 8131
PORT_REAL = 8132


def token_count() -> int:
    return len(REPLY_TEXT)


def full_generation_s() -> float:
    """把这整段回复生成完需要多久——三种形态必须用同一个数。"""
    return THINK_S + (token_count() - 1) * PER_TOKEN_S


# --------------------------------------------------------------------------
# 被测形态 A：现状（假流式）——直接在真实 app 上把 build_reply 换成慢模型
# --------------------------------------------------------------------------
_simulate_model_latency = {"on": True}


def make_fake_streaming_app() -> FastAPI:
    """返回真实项目的 app，但把模型层换成"有真实延迟"的版本。

    注意：改的**只有模型层**。ws.py / streaming.py 一行没动——
    这正是要证明的：响应层的代码决定了它能不能真流式，跟模型快慢无关。
    """
    import ai_lab.ws as ws_module

    def model_that_takes_time(payload: ChatRequest, settings: Settings | None = None) -> str:
        """签名刻意与真实的 build_reply 完全一致——这样替换才有意义。

        参数用 `object` 之类也能通过类型检查（函数参数是逆变的），但那样
        就失去了"我正在替换同一个接缝"的证明力。
        """
        del payload, settings
        if _simulate_model_latency["on"]:
            # 同步阻塞：整个回复生成完了才返回一个 str
            time.sleep(full_generation_s())
        return REPLY_TEXT

    ws_module.build_reply = model_that_takes_time

    from ai_lab.api import app

    return app


# --------------------------------------------------------------------------
# 被测形态 B：真流式——异步生成器签名（这是 W3 该有的形状）
# --------------------------------------------------------------------------
real_shape_app = FastAPI(title="真流式形状（脚本内演示，非项目现有代码）")


async def generate_tokens(model_input: str) -> AsyncIterator[str]:
    """真流式模型层：一边生成一边吐，**不等整段算完**。

    与形态 A 的关键差别只有一个——返回类型：

        build_reply(...) -> str                  ← 拿到的是一整段，必然假流式
        generate_tokens(...) -> AsyncIterator[str]  ← 拿到的是"半成品流"，才能真流式

    函数签名本身就是答案。一个返回 `str` 的模型层，无论上层怎么写，
    都不可能做到真流式。
    """
    del model_input
    await asyncio.sleep(THINK_S)  # 思考：产出第 0 个 token 之前的时间
    for char in REPLY_TEXT:
        yield char
        await asyncio.sleep(PER_TOKEN_S)


@real_shape_app.websocket("/ws/real")
async def ws_real(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.receive_text()

    sent = 0
    async for token in generate_tokens(""):
        sent += 1
        await websocket.send_json({"type": "token", "text": token, "index": sent})

    await websocket.send_json({"type": "done", "chunks": sent})


# --------------------------------------------------------------------------
# 起停服务（后台线程）
# --------------------------------------------------------------------------
class RunningServer:
    def __init__(self, app: FastAPI, port: int) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> RunningServer:
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


# --------------------------------------------------------------------------
# 客户端计时
# --------------------------------------------------------------------------
async def measure(url: str, *, headers: dict[str, str] | None = None) -> dict[str, float | int]:
    """计时：TTFT（第一个 token 帧到达）、总时长、帧数。

    计时起点放在**握手完成、请求发出之后**——否则握手的耗时会被算进 TTFT，
    把一个结构性问题（生成层不能流式）和一个环境开销混在一起。分不清就说明白：
    TTFT 要回答的是"请求发出后多久见到第一个字"，跟连接建立快慢无关。
    """
    payload = json.dumps({"message": "补液原则是什么？", "session_id": "probe", "stream": True})
    ttft: float | None = None
    tokens = 0

    async with connect(url, additional_headers=headers or {}) as websocket:
        started = time.perf_counter()  # ← 握手完成后才开始计时
        await websocket.send(payload)
        while True:
            message = json.loads(await websocket.recv())
            if message.get("type") == "token":
                tokens += 1
                if ttft is None:
                    ttft = time.perf_counter() - started
            elif message.get("type") == "done":
                break

    return {
        "ttft": ttft if ttft is not None else -1.0,
        "total": time.perf_counter() - started,
        "tokens": tokens,
    }


def report(label: str, result: dict[str, float | int], expectation: str) -> None:
    print(f"  {label}")
    print(f"      TTFT（首 token 到达） = {result['ttft']:6.3f}s")
    print(f"      总时长               = {result['total']:6.3f}s")
    print(f"      token 帧数           = {result['tokens']}")
    print(f"      预期                 : {expectation}")
    print()


# --------------------------------------------------------------------------
def show_source_evidence() -> None:
    print("=" * 78)
    print("第 0 节 · 先看源码里那一行（这就是全部原因）")
    print("=" * 78)
    print()
    root = Path(__file__).resolve().parents[1]
    targets = [
        ("src/ai_lab/ws.py", "reply = build_reply(payload, settings)"),
        ("src/ai_lab/stub_llm.py", "def build_reply(payload: ChatRequest, settings: Settings | None = None) -> str:"),
        ("src/ai_lab/streaming.py", "text = reply if reply is not None else build_reply("),
    ]
    for relative, needle in targets:
        path = root / relative
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().startswith(needle[:40]):
                print(f"  {relative}:{number}")
                print(f"      {line.strip()}")
                break
    print()
    print("  三个位置都是同一个形状：**先拿到完整的 str，再慢慢放出去**。")
    print("  `-> str` 这个返回类型决定了它拿不到半成品，也就只能假流式。")
    print()


def show_sleep_precision() -> None:
    """附注：本机的 sleep 精度。

    起因是实测节奏跟配置对不上：STREAM_DELAY_MS=20，但每个 token 实际间隔约 31ms。
    查清楚才发现是 Windows 的时钟粒度问题——**asyncio.sleep 会向上取整到
    15.6ms 的整数倍**（Windows 默认计时器 64Hz），而 time.sleep 是准的。

    影响：本机做流式观感演示时，实际节奏比配置的慢一半多。
    这也解释了为什么早先记录里"每 31ms 一个字"和"配置 20ms"看起来矛盾——
    两个数都是对的，是我没把差值追究到底。
    """
    print("=" * 78)
    print("第 3 节 · 附注：本机 sleep 的实际精度")
    print("=" * 78)
    print()
    print("  asyncio.sleep（流式节奏用的就是它）")
    for target in (0.005, 0.020, 0.050, 0.100):
        samples = asyncio.run(_sleep_samples(target))
        actual = statistics.median(samples)
        print(
            f"      目标 {target * 1000:6.1f}ms →  实际 {actual * 1000:6.2f}ms"
            f"   倍率 {actual / target:4.2f}×"
        )
    print()
    print("  对照 time.sleep（同步等待，CPython 在 Windows 上精度更高）")
    for target in (0.020, 0.050):
        samples = []
        for _ in range(7):
            t0 = time.perf_counter()
            time.sleep(target)
            samples.append(time.perf_counter() - t0)
        actual = statistics.median(samples)
        print(
            f"      目标 {target * 1000:6.1f}ms →  实际 {actual * 1000:6.2f}ms"
            f"   倍率 {actual / target:4.2f}×"
        )
    print()
    print("  规律：asyncio.sleep 向上取整到 15.6ms 的整数倍——")
    print("        5→15.6  20→31.2  50→62.4  100→109.2，跟实测基本吻合。")
    print("  注意这是**纯本地现象**：Linux 的事件循环不背这个包袱，部署到容器里")
    print("        节奏就是准的。也就是说它属于「本地看着坏、线上是好的」那一类，")
    print("        和 Day 4 的 X-Accel-Buffering 正好相反。")
    print()
    print("  对生产的提醒：这个 sleep 本身只是占位模型的演示道具，不值一提。")
    print("        但如果把 asyncio.sleep 用在**重试退避、心跳周期、限流窗口**上，")
    print("        在 Windows 上每次都会多睡最多 15.6ms——次数一多就会累积成偏差。")
    print()


async def _sleep_samples(target: float, count: int = 9) -> list[float]:
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        await asyncio.sleep(target)
        samples.append(time.perf_counter() - started)
    return samples


def main() -> int:
    settings = get_settings()
    delay_s = settings.stream_delay_ms / 1000.0
    if abs(delay_s - PER_TOKEN_S) > 1e-9:
        print(f"注意：.env 的 STREAM_DELAY_MS={settings.stream_delay_ms}，"
              f"脚本按 {PER_TOKEN_S * 1000:.0f}ms 估算，秒数会有偏差。")

    show_source_evidence()

    print("=" * 78)
    print("第 1 节 · 三种形态实测（服务由脚本自己起，端口 8131 / 8132）")
    print("=" * 78)
    print()
    print(f"  模型规格：第 k 个 token 于 {THINK_S}s + k×{PER_TOKEN_S}s 就绪")
    print(f"  回复长度：{token_count()} 个字符 token")
    print(f"  生成完整段回复需要：{full_generation_s():.3f}s")
    print(f"  响应层的人工节奏（STREAM_DELAY_MS）：{settings.stream_delay_ms}ms/token")
    print()

    auth = {"X-API-Key": settings.client_api_key}

    # ---------- ①② 形态 A：现状 ----------
    fake_app = make_fake_streaming_app()
    with RunningServer(fake_app, PORT_FAKE):
        url_fake = f"ws://127.0.0.1:{PORT_FAKE}/ws/chat"

        print("  【① 今天的实况】模型是占位实现，生成耗时 ≈ 0")
        _simulate_model_latency["on"] = False
        result_1 = asyncio.run(measure(url_fake, headers=auth))
        report(
            "现状代码 + 无延迟模型：",
            result_1,
            "TTFT ≈ 20ms —— 看着非常像流式，幻觉由此产生",
        )

        print("  【② 同一个接口，换成有真实延迟的模型】响应层代码一行没改")
        _simulate_model_latency["on"] = True
        result_2 = asyncio.run(measure(url_fake, headers=auth))
        report(
            "现状代码 + 有延迟模型：",
            result_2,
            f"TTFT ≈ {full_generation_s():.2f}s —— 用户必须等整段生成完",
        )

    # ---------- ③ 形态 B：真流式 ----------
    with RunningServer(real_shape_app, PORT_REAL):
        print("  【③ 真流式形状】同一份模型成本，但改成异步生成器签名")
        result_3 = asyncio.run(measure(f"ws://127.0.0.1:{PORT_REAL}/ws/real"))
        report(
            "异步生成器形状：",
            result_3,
            f"TTFT ≈ {THINK_S:.2f}s —— 只等第一个 token",
        )

    # ---------- 结论 ----------
    print("=" * 78)
    print("第 2 节 · 结论")
    print("=" * 78)
    print()
    print(f"  同一个模型，同样的总工作量，用户体感差多少：")
    print()
    print(f"    假流式 TTFT = {result_2['ttft']:.2f}s ←→ 真流式 TTFT = {result_3['ttft']:.2f}s"
          f"    相差 {result_2['ttft'] / result_3['ttft']:.1f} 倍")
    print(f"    假流式总时长 {result_2['total']:.2f}s ←→ 真流式总时长 {result_3['total']:.2f}s"
          f"    多花 {result_2['total'] - result_3['total']:.2f}s")
    print()
    print("  两个数字都是必然的，不是实现不够优：")
    print("    ① TTFT 差 = 整段生成时间。因为 `-> str` 决定了第一个 token")
    print("       不可能早于最后一个 token 生成完毕。")
    print("    ② 总时长差 ≈ 人工节奏的总和。生成的时间在同一个请求里被付了两遍：")
    print("       一遍在 build_reply 里真等，一遍在 tokenize 的 sleep 里假等。")
    print()
    print("  结论：传输层是真流式（帧确实一条条走），生成层是假流式（内容一次性产生）。")
    print("        这件事在 W3 接真实模型时不做改造，首字延迟会原封不动地翻上去。")
    print()

    show_sleep_precision()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
