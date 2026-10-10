"""SSE（Server-Sent Events）流式响应。

为什么聊天类接口要流式：
  用户感知的是【首字延迟】，不是总时长。非流式下要等模型把 500 字全写完（10~30 秒）
  才有任何反馈；流式下第一句 300ms 就到了。这不是"更酷"，是体验的量级差异。

SSE 协议本体（别被名字吓到，它就是"长连接 + 纯文本约定"）：
  一个帧 = 若干「字段: 值」行 + 一个空行收尾
      event: token          ← 事件名，浏览器 addEventListener("token", ...) 监听
      data: {"text":"补"}    ← 数据体；可写多行 data:，客户端用 \n 拼回
      id: 7                 ← 事件 ID，断线重连时浏览器会带 Last-Event-ID 回来
      retry: 3000           ← 断线后重连间隔（毫秒）
  以「:」开头的行是注释（心跳），客户端必须忽略——用来防代理/网关超时断连。

  注意协议里没有"结束标记"这种东西：服务端关掉响应体流，客户端就收到 onerror
  或 readyState=CLOSED。所以实践中大家自己约定一个结束事件（见下面 event: done），
  OpenAI 用的是 `data: [DONE]` 这个惯例。

Day 4 故意不引 sse-starlette：手写过一次帧格式，以后遇到"帧解析错误""事件名不对"
才知道该往哪儿看。
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final

from ai_lab.config import Settings
from ai_lab.logging_setup import get_logger
from ai_lab.schemas import ChatRequest
from ai_lab.stub_llm import MODEL_NAME, build_reply

logger = get_logger(__name__)

SSE_MEDIA_TYPE: Final = "text/event-stream"

# 三个响应头，每一个都对应一类真实的"流被卡住"事故：
#   Cache-Control: no-cache   → 别让中间层缓存住整段响应（缓存=用户最后才看到全部）
#   X-Accel-Buffering: no     → 明确要求 Nginx 关闭代理缓冲（不写这个，Nginx 会
#                               把 token 攒在自己的缓冲区里，用户看到的仍是"一次性到达"）
#   Connection: keep-alive    → 长连接语义（HTTP/1.1 下是提示，HTTP/2 里无意义）
SSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def sse_frame(
    data: str,
    *,
    event: str | None = None,
    event_id: str | None = None,
    retry_ms: int | None = None,
) -> str:
    """按 SSE 规范编码一个帧。

    关键细节：data 里若含换行，必须【每一行都加 data: 前缀】。
    直接写 `data: {带换行的文本}\\n\\n` 会怎样？客户端只收到第一行，
    后面的内容被当成"非法字段行"直接丢弃——这是最典型的静默数据丢失。
    """
    lines: list[str] = []
    if event is not None:
        lines.append(f"event: {event}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    lines.extend(f"data: {line}" for line in data.split("\n"))
    return "\n".join(lines) + "\n\n"


def sse_json_frame(payload: dict[str, Any], *, event: str | None = None) -> str:
    """结构化数据统一走 JSON。

    两个好处：
      1. json.dumps 会把换行转义成 \\n，天然保住"一帧一行 data"的约束（上面那个坑）；
      2. 客户端解析稳定——不用猜"这段文本哪里是分隔符"。
    ensure_ascii=False 让中文以原字符出现，抓包和 curl 时肉眼可读。
    """
    return sse_frame(json.dumps(payload, ensure_ascii=False), event=event)


def sse_comment(text: str = "ka") -> str:
    """注释帧（心跳）：客户端忽略，但能让中间层看到"连接还活着"。

    没有心跳 + 长时间无数据的连接，会被 Nginx / 负载均衡 / 云厂商 API 网关
    按空闲超时掐掉——表现为"用户读长回答读一半，连接突然断了"。
    """
    return f": {text}\n\n"


async def tokenize(text: str, *, delay_s: float = 0.0, chunk_size: int = 1) -> AsyncIterator[str]:
    """把完整文本切成"一个 token 一个 token"地吐出来。

    真实的 token 是分词器的输出（一个 token ≈ 1~2 个汉字或半个英文词），
    这里按字符切只是为了肉眼观察方便。

    ⚠️ 它产出的是**假流式**：入参 `text` 是一整段已经生成完毕的字符串，
    这里的 sleep 只是把成品慢慢放出来，**不可能让上游提前产出任何东西**。
    真流式要求上游是 `AsyncIterator[str]`，产出节奏由模型层掌握。
    两者在首字延迟上的差距是数量级的，见 scripts/probe_real_vs_fake_streaming.py。

    另注：`asyncio.sleep` 在 Windows 上会向上取整到 15.6ms 的整数倍，
    所以 STREAM_DELAY_MS=20 实测节奏约 31ms/token（Linux 上无此问题）。
    """
    for i in range(0, len(text), chunk_size):
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        yield text[i : i + chunk_size]


async def sse_chat_stream(
    payload: ChatRequest,
    settings: Settings,
    *,
    reply: str | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    heartbeat_every: int = 20,
) -> AsyncIterator[str]:
    """逐帧产出 SSE 数据：meta → token × N（夹心跳）→ done。

    两个刻意的设计：

    1. `is_disconnected` 是【传入的回调】而不是直接吃个 Request 对象。
       好处是测试里可以塞一个返回 True 的假函数，把"客户端中途断开"这个
       极难复现的场景变成一条普通断言（见 tests/test_streaming.py）。
       生产里传 request.is_disconnected 即可。

    2. 断开检查放在【每发一个 token 前】：用户关掉页面后，模型还在傻算 3000 个
       token 就是纯烧钱。流式端点必须尽早发现自己已经没人听了。
    """
    text = reply if reply is not None else build_reply(payload.model_copy(update={"stream": True}), settings)
    started = time.perf_counter()

    yield sse_json_frame(
        {
            "session_id": payload.session_id,
            "model": MODEL_NAME,
            "total_chars": len(text),
        },
        event="meta",
    )

    delay_s = settings.stream_delay_ms / 1000.0
    sent = 0
    first_token_at: float | None = None

    async for token in tokenize(text, delay_s=delay_s):
        if is_disconnected is not None and await is_disconnected():
            logger.warning(
                "客户端已断开，提前终止流式生成（省下的就是钱）",
                extra={"session_id": payload.session_id, "sent_chars": sent},
            )
            return

        sent += 1
        if first_token_at is None:
            first_token_at = time.perf_counter() - started
        yield sse_json_frame({"text": token, "index": sent}, event="token")

        # 心跳夹在 token 之间：每 20 个 token 插一条注释帧
        if sent % heartbeat_every == 0:
            yield sse_comment(f"ka-{sent}")

    yield sse_json_frame(
        {
            "session_id": payload.session_id,
            "chunks": sent,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            # 首字延迟是流式体验的核心指标，生产里一定要采集它
            "ttft_ms": int((first_token_at or 0) * 1000),
        },
        event="done",
    )
