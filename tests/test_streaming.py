"""SSE 流式响应测试。

这一天最容易被糊弄过去的地方是："看起来能流式" ≠ "断言过它流式"。
TestClient 的 `client.stream(...)` + 逐帧解析，能把「分了几帧、帧里是什么、
响应头有没有关掉代理缓冲」全部钉成断言。

**但时间维度不能用 TestClient 断言**（Day 4 实测踩到）：
  TestClient 的 ASGI 传输层会把整个响应体收完再交给 httpx，所以在它眼里
  「首帧 0.000s、末帧 0.000s」——帧内容、顺序全对，**只有时序是假的**。
  真实世界里的缓冲者（Nginx、云负载均衡、企业代理）症状一模一样：
  内容没错、时序全错。所以：
      内容与顺序 → 用 TestClient 断言（快、稳定、可进 CI）
      时序（首帧早、末帧晚）→ 必须打真实 HTTP，见 scripts/smoke_api.py 与
                              scripts/probe_stream_timing.py（后者会把两者并排打印）

另外本文件里有两条纯单元测试（直接调用生成器、塞一个假的 is_disconnected），
因为"客户端中途断开"这种时序场景用 TestClient 很难稳定复现。
能被注入的依赖，就该拿来测试。
"""

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ai_lab.api import app
from ai_lab.config import Settings
from ai_lab.deps import get_settings_dep
from ai_lab.schemas import ChatRequest
from ai_lab.streaming import (
    sse_chat_stream,
    sse_comment,
    sse_frame,
    sse_json_frame,
    tokenize,
)

client = TestClient(app)

TEST_API_KEY = "client-key-for-tests"
TEST_LLM_KEY = "sk-upstream-key-for-tests"
AUTH = {"X-API-Key": TEST_API_KEY}

VALID_PAYLOAD: dict[str, object] = {
    "message": "糖尿病酮症酸中毒的补液原则是什么？",
    "session_id": "sess-20260930-001",
}


def _fake_settings() -> Settings:
    """假配置。stream_delay_ms=0 是关键：真实值 20ms 会让每个流式用例白等一两秒，
    测试里把它关掉——"配置驱动的行为可以被测试"的实际用途。"""
    return Settings(
        _env_file=None,
        client_api_key=TEST_API_KEY,
        llm_api_key=TEST_LLM_KEY,
        llm_base_url="https://fake.example/v1",
        llm_timeout=5.0,
        max_concurrent=2,
        max_retries=1,
        stream_delay_ms=0.0,
    )


@pytest.fixture(autouse=True)
def fake_settings() -> Iterator[None]:
    app.dependency_overrides[get_settings_dep] = _fake_settings
    yield
    app.dependency_overrides.clear()


def parse_sse_frames(raw: str) -> tuple[list[tuple[str | None, str]], list[str]]:
    """把一个完整的 SSE 响应体拆成 (事件列表, 注释列表)。

    帧之间用空行分隔，所以按 "\\n\\n" 切分即可。
    每帧内部是若干「字段: 值」行，data 可有多行（按协议应拼接回 \n）。
    """
    events: list[tuple[str | None, str]] = []
    comments: list[str] = []

    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        if block.startswith(":"):
            comments.append(block)
            continue

        event: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        events.append((event, "\n".join(data_lines)))

    return events, comments


# ------------------------------------------------------- 帧格式（协议层单元测试）


def test_sse_frame_minimal() -> None:
    """最小帧：一行 data + 空行收尾。"""
    assert sse_frame("hello") == "data: hello\n\n"


def test_sse_frame_with_metadata() -> None:
    """event / id / retry 字段都出现在 data 之前。"""
    frame = sse_frame("hi", event="token", event_id="7", retry_ms=3000)

    assert frame == "event: token\nid: 7\nretry: 3000\ndata: hi\n\n"


def test_sse_frame_gives_every_data_line_its_prefix() -> None:
    """data 里含换行时，每行都要加 data: 前缀。

    这是 SSE 里最容易踩的静默丢数据陷阱：只写一行 `data: 第一行\\n第二行` 的话，
    客户端只会拿到第一行，第二行被当成非法字段行丢弃——没有任何报错。
    """
    frame = sse_frame("第一行\n第二行")

    assert frame == "data: 第一行\ndata: 第二行\n\n"
    # 客户端按协议拼回来，内容与原始文本一致
    block = frame.strip()
    restored = "\n".join(
        line.removeprefix("data: ") for line in block.split("\n") if line.startswith("data: ")
    )
    assert restored == "第一行\n第二行"


def test_sse_json_frame_keeps_single_data_line() -> None:
    """结构化数据走 json.dumps：换行被转义，一帧仍只有一行 data。"""
    frame = sse_json_frame({"text": "带\n换行和\"引号\"的内容"}, event="token")

    data_line = frame.split("\n")[1]
    assert data_line.startswith("data: ")
    assert json.loads(data_line.removeprefix("data: "))["text"] == "带\n换行和\"引号\"的内容"
    assert len(frame.strip().split("\n")) == 2  # event 一行 + data 一行


def test_sse_comment_is_a_comment_frame() -> None:
    """心跳是注释帧：以冒号开头，客户端必须忽略。"""
    assert sse_comment("ka-20") == ": ka-20\n\n"


# ------------------------------------------------------- 端点行为


def test_chat_stream_requires_api_key() -> None:
    """流式端点同样要鉴权——别因为"它是流"就忘了挂依赖。"""
    resp = client.post("/chat/stream", json=VALID_PAYLOAD)

    assert resp.status_code == 401


def test_chat_stream_rejects_invalid_body() -> None:
    """校验发生在流开始【之前】：非法请求体不会先开流再报错。"""
    resp = client.post("/chat/stream", json={**VALID_PAYLOAD, "message": ""}, headers=AUTH)

    assert resp.status_code == 422


def test_chat_stream_headers_disable_buffering() -> None:
    """响应头必须明确要求中间层不要缓冲（本地 curl 测不出来的那类坑）。"""
    with client.stream("POST", "/chat/stream", json=VALID_PAYLOAD, headers=AUTH) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["x-accel-buffering"] == "no"
        assert resp.headers["cache-control"] == "no-cache"


def test_chat_stream_emits_meta_tokens_done() -> None:
    """帧序列：meta → token × N → done，且 token 拼回来等于完整回复。"""
    resp = client.post("/chat/stream", json=VALID_PAYLOAD, headers=AUTH)
    events, _comments = parse_sse_frames(resp.text)

    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert set(names) == {"meta", "token", "done"}

    meta = json.loads(events[0][1])
    assert meta["session_id"] == "sess-20260930-001"
    assert meta["total_chars"] > 10

    tokens = [json.loads(data)["text"] for name, data in events if name == "token"]
    assert len(tokens) > 10  # 真的分了多帧，不是一次性一坨
    assert "".join(tokens).startswith("[stub] 收到")
    # 索引连续递增：客户端可据此检测丢帧
    assert [json.loads(data)["index"] for name, data in events if name == "token"] == list(
        range(1, len(tokens) + 1)
    )

    done = json.loads(events[-1][1])
    assert done["chunks"] == len(tokens)
    assert done["ttft_ms"] >= 0
    assert done["latency_ms"] >= done["ttft_ms"]


def test_chat_stream_iterates_line_by_line() -> None:
    """用 iter_lines() 逐行读：证明数据是"边生成边出来"的流，而不是攒好一次给。"""
    with client.stream("POST", "/chat/stream", json=VALID_PAYLOAD, headers=AUTH) as resp:
        lines = [line for line in resp.iter_lines() if line]

    assert any(line.startswith("event: meta") for line in lines)
    assert any(line.startswith("event: done") for line in lines)
    # 每个 token 帧两行（event + data），所以行数远多于帧数才有意义
    assert len([line for line in lines if line.startswith("data:")]) > 10


def test_chat_with_stream_true_returns_event_stream() -> None:
    """同一个 /chat 端点：stream=true 走 SSE，stream=false 走 JSON。"""
    with client.stream(
        "POST", "/chat", json={**VALID_PAYLOAD, "stream": True}, headers=AUTH
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.read().decode()

    events, _ = parse_sse_frames(body)
    assert events[0][0] == "meta"
    assert events[-1][0] == "done"


def test_chat_without_stream_still_returns_json() -> None:
    """回归保护：加了流式分支后，原来的 JSON 路径不能被带坏。"""
    resp = client.post("/chat", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert set(resp.json()) == {"reply", "session_id", "model", "latency_ms"}


def test_stream_emits_heartbeat_comments() -> None:
    """长回复中间会夹心跳注释帧，防止中间层按空闲超时掐断连接。"""
    resp = client.post("/chat/stream", json=VALID_PAYLOAD, headers=AUTH)
    _events, comments = parse_sse_frames(resp.text)

    assert comments, "长回复里应当出现心跳注释帧"
    assert all(c.startswith(":") for c in comments)


def test_openapi_documents_stream_route() -> None:
    """流式端点的文档也要有：客户端才知道这是个 text/event-stream 接口。"""
    schema = client.get("/openapi.json").json()

    assert "/chat/stream" in schema["paths"]
    content = schema["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]
    assert "text/event-stream" in content


# ------------------------------------------------------- 断开处理（可注入回调的收益）


def test_stream_stops_early_when_client_disconnects() -> None:
    """客户端断开 → 生成器立刻停止，不再继续"算"下去（省的就是钱）。

    这里直接调用生成器并注入一个假 is_disconnected —— 时序场景变成普通断言。
    """
    payload = ChatRequest(message="你好", session_id="sess-x")
    settings = Settings(
        _env_file=None,
        client_api_key=TEST_API_KEY,
        llm_api_key="sk-x",
        stream_delay_ms=0.0,
    )
    calls = {"n": 0}

    async def fake_is_disconnected() -> bool:
        calls["n"] += 1
        return calls["n"] > 3  # 第 4 次检查时"断开"

    async def collect() -> list[tuple[str | None, str]]:
        frames = [
            frame
            async for frame in sse_chat_stream(
                payload,
                settings,
                reply="一二三四五六七八九十",  # 10 个 token，正常会发完
                is_disconnected=fake_is_disconnected,
                heartbeat_every=100,
            )
        ]
        return parse_sse_frames("".join(frames))[0]

    events = asyncio.run(collect())

    names = [name for name, _ in events]
    assert names == ["meta", "token", "token", "token"]  # 没有 done 帧
    assert calls["n"] == 4  # 每发一个 token 前检查一次，第 4 次就退出


def test_stream_without_disconnect_callback_sends_everything() -> None:
    """不传回调也能正常工作（回调是可选的）。"""
    payload = ChatRequest(message="你好", session_id="sess-x")
    settings = Settings(
        _env_file=None,
        client_api_key=TEST_API_KEY,
        llm_api_key="sk-x",
        stream_delay_ms=0.0,
    )

    async def collect() -> list[tuple[str | None, str]]:
        frames = [f async for f in sse_chat_stream(payload, settings, reply="abc")]
        return parse_sse_frames("".join(frames))[0]

    events = asyncio.run(collect())
    tokens = [json.loads(d)["text"] for n, d in events if n == "token"]

    assert tokens == ["a", "b", "c"]
    assert events[-1][0] == "done"


def test_tokenize_with_delay_yields_all_chunks() -> None:
    """带延迟的分词器依旧按顺序吐完每个 token（覆盖真实 delay 分支）。"""

    async def collect() -> list[str]:
        return [chunk async for chunk in tokenize("abc", delay_s=0.001)]

    assert asyncio.run(collect()) == ["a", "b", "c"]


# 关于"流式时序"的断言为什么不在这个文件里：
#   实测（见 scripts/probe_stream_timing.py）——TestClient 首帧 0.000s / 末帧 0.000s，
#   真实 HTTP 首帧 0.000s / 末帧 2.708s。同一份代码，时序断言一个成立一个不成立，
#   差别全在"谁替你缓冲了数据"。所以时序检查放在 scripts/smoke_api.py 里打真实服务。
