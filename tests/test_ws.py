"""WebSocket 端点测试。

TestClient 的 websocket_connect 是个同步上下文管理器，用起来和普通 HTTP 请求差不多：

    with client.websocket_connect("/ws/chat", headers=...) as ws:
        assert ws.receive_json()["type"] == "welcome"
        ws.send_json({"message": "你好", "session_id": "sess-1"})
        assert ws.receive_json()["type"] == "reply"

**但它的局限和 Day 4 完全一样**（这条要在 Day 7 的笔记里写进去）：
TestClient 不经过真实的网络栈——协议升级握手、帧的编解码、背压、缓冲都是模拟的。
所以「握手被拒绝时客户端到底看到什么」这类行为，最终必须用真实客户端复核一次
（见 scripts/ws_client_demo.py）。框架测试证不了协议行为。
"""

import asyncio
import json
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ai_lab.api import app
from ai_lab.config import Settings
from ai_lab.deps import get_settings_dep
from ai_lab.ws import ConnectionManager, get_manager

# 两把钥匙给不同的值（config.py 的校验禁止相同）
TEST_CLIENT_KEY = "client-key-for-tests"
TEST_LLM_KEY = "sk-upstream-key-for-tests"

client = TestClient(app)

VALID_MESSAGE = {"message": "补液原则是什么？", "session_id": "sess-ws-001"}


def _fake_settings() -> Settings:
    # stream_delay_ms=0 让流式用例瞬间跑完（复用 Day 4 的做法）
    return Settings(
        _env_file=None,
        client_api_key=TEST_CLIENT_KEY,
        llm_api_key=TEST_LLM_KEY,
        stream_delay_ms=0.0,
    )


@pytest.fixture(autouse=True)
def isolated_manager() -> Iterator[ConnectionManager]:
    """每个用例换一个全新的连接管理器。

    这就是 get_manager 存在的理由：manager 是可变全局状态，
    不隔离的话用例之间会互相污染（上一个没断开，下一个的在线数就多 1）。
    把全局状态包成依赖，就能像换配置一样把它换掉——Day 3 那套在这里第二次生效。
    """
    fresh = ConnectionManager()
    app.dependency_overrides[get_manager] = lambda: fresh
    yield fresh
    app.dependency_overrides.pop(get_manager, None)


@pytest.fixture(autouse=True)
def fake_settings() -> Iterator[None]:
    app.dependency_overrides[get_settings_dep] = _fake_settings
    yield
    app.dependency_overrides.pop(get_settings_dep, None)


# --------------------------------------------------------------- 鉴权

def test_handshake_rejected_without_credentials() -> None:
    """没有凭据 → 握手就被拒绝，连不上。

    注意这里必须用 accept 之前的 close 实现（见 ws.py 的说明）：
    只有这样，客户端看到的是"连接失败"，而不是"连上之后立刻被踢"。
    """
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat"):
            pass


def test_handshake_rejected_with_wrong_credentials() -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat", headers={"X-API-Key": "wrong"}):
            pass


def test_upstream_llm_key_is_not_accepted_on_websocket() -> None:
    """上游 LLM 密钥同样不能当门禁卡——WS 这条路径也要守住。

    独立测一遍不是重复：HTTP 与 WS 走的是两套鉴权代码（一个用 APIKeyHeader 依赖，
    一个是手写 _is_authorized），只在一边加限制是真实项目里最常见的漏洞形态。
    """
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_LLM_KEY}):
            pass


def test_accepts_credentials_via_header() -> None:
    """非浏览器客户端（Python websockets / 移动端）走请求头。"""
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        assert ws.receive_json()["type"] == "welcome"


def test_accepts_credentials_via_query_param() -> None:
    """浏览器走查询参数——因为浏览器的 WebSocket API 根本设不了自定义请求头。

    这条测试的价值是把它钉住：将来谁"为了安全"把查询参数这条路删掉，
    浏览器客户端会直接连不上，而单元测试会先红。
    """
    with client.websocket_connect(f"/ws/chat?api_key={TEST_CLIENT_KEY}") as ws:
        assert ws.receive_json()["type"] == "welcome"


def test_header_takes_priority_over_query_param() -> None:
    """两个来源同时给且冲突时，请求头优先——这条规则必须确定，不能靠实现顺序碰运气。

    注意握手被拒的异常是在 **进入上下文时** 抛的，不是在 websocket_connect() 调用时，
    所以 `with pytest.raises` 必须套住整个 `with`。
    """
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws/chat?api_key={TEST_CLIENT_KEY}", headers={"X-API-Key": "wrong"}
        ):
            pass


# --------------------------------------------------------------- 收发

def test_welcome_frame_reports_active_connections() -> None:
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        welcome = ws.receive_json()

    assert welcome["type"] == "welcome"
    assert welcome["active_connections"] == 1
    assert "model" in welcome


def test_send_message_and_receive_reply() -> None:
    """最核心的一条：收一条、回一条。"""
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        ws.send_json(VALID_MESSAGE)
        reply = ws.receive_json()

    assert reply["type"] == "reply"
    assert reply["session_id"] == "sess-ws-001"
    # 占位实现（stub_llm）不回显原文，只汇报"收到几个字"——8 正好是"补液原则是什么？"的长度。
    # 断言它等于 8，能证明请求体完整地穿过了 WS → 校验 → 模型层这条链路。
    assert "8 字" in reply["reply"]
    assert reply["latency_ms"] >= 0


def test_multiple_turns_on_one_connection() -> None:
    """一条连接可以聊多轮——这正是 WS 相对"每次都新建请求"的价值。

    HTTP 每次对话都要重新握手、重新鉴权；WS 建立一次，后续轮次只剩帧往返。
    """
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        for index in range(3):
            ws.send_json({**VALID_MESSAGE, "session_id": f"sess-ws-{index}"})
            assert ws.receive_json()["session_id"] == f"sess-ws-{index}"


def test_stream_true_sends_tokens_then_done() -> None:
    """WS 上做流式不需要发明协议：直接一条条发就行。

    对照 Day 4 的 SSE：那边要手工拼 `data: ` 前缀、空行分隔符、处理多行前缀坑、
    还要惦记 X-Accel-Buffering。这里是"能想到的最简单做法"。
    代价在别处——重连、序号、心跳得自己写。
    """
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        ws.send_json({**VALID_MESSAGE, "stream": True})

        frames = [ws.receive_json()]
        while frames[-1]["type"] != "done":
            frames.append(ws.receive_json())

    tokens = [f for f in frames if f["type"] == "token"]
    assert len(tokens) > 5
    assert frames[-1]["type"] == "done"
    assert frames[-1]["chunks"] == len(tokens)
    # 序号从 1 开始且连续——客户端靠它检测丢帧
    assert [t["index"] for t in tokens] == list(range(1, len(tokens) + 1))


# --------------------------------------------------------------- 校验

def test_invalid_json_gets_error_frame_not_crash() -> None:
    """WS 里没有 422——状态码是 HTTP 的概念，这里已经是升级后的帧。

    所以错误协议必须自己发明。断言三件事：连接没断、错误结构可解析、可继续对话。
    """
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        ws.send_text("这不是 JSON")

        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["reason"] == "invalid_request"

        # 关键：出错之后连接还活着，能继续聊（HTTP 里 422 之后请求就结束了）
        ws.send_json(VALID_MESSAGE)
        assert ws.receive_json()["type"] == "reply"


def test_empty_message_gets_structured_error() -> None:
    """复用 ChatRequest = 复用同一套校验规则，错误结构也与 422 同构。

    这条是"任何外部输入都是边界"的实证：REST 和 WS 用同一个模型、同一套约束，
    换协议不用重写校验。
    """
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        ws.send_json({"message": "", "session_id": "sess-ws-001"})

        error = ws.receive_json()

    assert error["type"] == "error"
    assert error["errors"][0]["loc"] == ["message"]
    assert error["errors"][0]["type"] == "string_too_short"


def test_bad_session_id_is_rejected_like_rest() -> None:
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        ws.send_json({"message": "你好", "session_id": "非法 id 有空格"})

        assert ws.receive_json()["errors"][0]["type"] == "string_pattern_mismatch"


# --------------------------------------------------------------- 连接管理

def test_disconnect_is_removed_from_active_set(isolated_manager: ConnectionManager) -> None:
    """断开之后必须从集合里摘掉——否则在线数会虚高，广播一直往死连接发。"""
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        ws.receive_json()  # welcome
        assert isolated_manager.count == 1

    assert isolated_manager.count == 0


def test_broadcast_notifies_other_connections(isolated_manager: ConnectionManager) -> None:
    """服务端主动推给多个客户端——这件 SSE 做起来别扭、WS 做起来自然的事。"""
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as first:
        assert first.receive_json()["active_connections"] == 1

        with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as second:
            assert second.receive_json()["active_connections"] == 2

            # 第一个连接【没有发任何东西】，却收到了服务端推来的通知
            pushed = first.receive_json()
            assert pushed["type"] == "joined"
            assert pushed["active_connections"] == 2

    assert isolated_manager.count == 0


class _UnsendableSocket:
    """一个"能连上但发不出去"的假连接，用来验证广播会摘掉死连接。"""

    def __init__(self) -> None:
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


def test_broadcast_drops_dead_connections_without_raising() -> None:
    """一个坏连接不能拖垮整轮广播。

    断开的连接在集合里可能还留着一小会儿（服务端要等到读/写失败才知道），
    所以广播必须容错：失败就摘掉，继续发给别人。
    """
    manager = ConnectionManager()
    dead = _UnsendableSocket()

    asyncio.run(manager.connect(cast(WebSocket, dead)))

    assert manager.count == 1

    delivered = asyncio.run(manager.broadcast({"type": "hello"}))

    assert delivered == 0  # 一条都没送出去
    assert manager.count == 0  # 死连接已被摘掉，不会每次广播都再失败一遍


def test_disconnect_is_idempotent() -> None:
    """重复断开不抛异常——清理动作要幂等（和 W1 的重试幂等是同一个道理）。"""
    manager = ConnectionManager()
    socket = cast(WebSocket, _UnsendableSocket())

    manager.disconnect(socket)
    manager.disconnect(socket)

    assert manager.count == 0


def test_get_manager_returns_shared_singleton() -> None:
    """默认实现返回模块级单例——一个进程里所有连接共用同一个管理器。

    这条看着像废话，但它覆盖的正是「**没被测试替身替换时**」的那条路径：
    上面每个用例都覆盖了 get_manager，如果没人测默认实现，
    生产环境真正会跑的那一行就一次都没被执行过——覆盖率会诚实地把它标红
    （这正是我们要求 100% 覆盖率的意义：它逼你发现"测试只测了替身"）。
    """
    assert isinstance(get_manager(), ConnectionManager)
    assert get_manager() is get_manager()


def test_welcome_and_error_frames_are_valid_json() -> None:
    """所有帧都必须是合法 JSON——含中文时 ensure_ascii=False 才肉眼可读。"""
    with client.websocket_connect("/ws/chat", headers={"X-API-Key": TEST_CLIENT_KEY}) as ws:
        raw = ws.receive_text()
        parsed = json.loads(raw)

    assert parsed["type"] == "welcome"
