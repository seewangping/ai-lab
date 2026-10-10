"""WebSocket 端点：它和 SSE 是【两种取舍】，不是新旧替代关系。

Day 5 的验收点是说清「聊天回复为什么选 SSE 而不是 WS」，答案有三条，
没有一条是"哪个新"：

  1. **聊天是单向流**：客户端发一条，服务端流式回一段。SSE 天生就是这个形状；
     WS 的双向能力在这个场景里是闲置的——为用不上的能力付复杂度不划算。
  2. **SSE 就是一个普通的 HTTP 响应**（body 是 text/event-stream），因此白送一整套
     现成设施：状态码、请求头、Cookie、CORS、认证中间件、HTTP/2 多路复用、
     以及 Nginx / 云网关的转发规则。WS 要先做协议升级（101 Switching Protocols），
     升级之后的帧不再有 HTTP 语义，上面那些设施全部失效，网关得单独为它开口子。
  3. **断线重连**：SSE 的 EventSource 由浏览器内置重连并自动带上 Last-Event-ID，
     服务端据此续传；WS 断了要自己写重连、自己定义"从哪继续"的协议。

WS 真正该上的场景是这两类，本文件演示了第二类：
  - 客户端要**持续往服务端推**数据（协同编辑、游戏操作流）
  - 服务端要**主动推给多个客户端**（下面的广播：有人连上/断开就通知其他在线的人）

对照 C#：ASP.NET Core 里是 app.UseWebSockets() + 中间件 AcceptWebSocketAsync；
FastAPI 把它做成路由装饰器，形状更接近 SignalR 的 Hub。
"""

import json
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette import status

from ai_lab.config import Settings
from ai_lab.deps import SettingsDep
from ai_lab.schemas import ChatRequest
from ai_lab.streaming import tokenize
from ai_lab.stub_llm import MODEL_NAME, build_reply

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """活跃连接集合。

    为什么需要它：WS 的连接是**有状态**的。HTTP 请求之间互不相识，
    但 WS 一旦建立，服务端就得记住"现在有哪些人在线"——这是 HTTP 里不存在的负担，
    也是它换来双向推送必须付的代价。
    """

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self._active)

    async def connect(self, websocket: WebSocket) -> int:
        """接受握手并登记，返回登记后的在线数。"""
        await websocket.accept()
        self._active.add(websocket)
        return len(self._active)

    def disconnect(self, websocket: WebSocket) -> int:
        """摘掉一个连接，返回剩余在线数。

        用 discard 而不是 remove：重复断开（客户端主动关 + 服务端异常退出）
        不该抛 KeyError。这类"清理动作要幂等"的思路和 W1 里重试要幂等是同一件事。
        """
        self._active.discard(websocket)
        return len(self._active)

    async def broadcast(self, message: dict[str, Any], *, exclude: WebSocket | None = None) -> int:
        """给所有在线连接推同一条消息，返回实际送达数。

        三个实现细节，每个都对应一类线上事故：
          1. `list(self._active)` 先拷贝——发送过程中可能有人断开并修改这个集合，
             直接迭代会抛 "Set changed size during iteration"。
          2. 逐个 try/except：**一个坏连接不能拖垮整轮广播**。这里故意捕获宽泛的
             Exception——只要 send 失败，这个连接就不可用了，具体是哪种异常不重要，
             重要的是把它摘掉，别让它每次广播都再失败一遍。
          3. 异步 send 是"排队"不是"送达"。所以广播天然是尽力而为，
             想保证送达得自己加 ack + 补发。
        """
        payload = json.dumps(message, ensure_ascii=False)
        delivered = 0
        for websocket in list(self._active):
            if websocket is exclude:
                continue
            try:
                await websocket.send_text(payload)
                delivered += 1
            except Exception:
                self._active.discard(websocket)
        return delivered


manager = ConnectionManager()


def get_manager() -> ConnectionManager:
    """把全局连接管理器包一层依赖函数——理由和 Day 3 的 get_settings_dep 完全一样：
    **可被替换**。

    为什么这件事在 WS 上比在别处更要紧：这个对象是**可变全局状态**，
    测试之间会互相污染。上一个用例没断开干净，下一个用例的"在线数=1"断言
    就会莫名其妙地变成 2。包成依赖之后，每个测试用例可以换上自己的全新实例。

    换句话说：把可变全局状态藏进依赖注入，是让它可以被隔离的标准做法。
    """
    return manager


# 必须写在 ConnectionManager 与 get_manager 之后——Annotated[...] 在模块加载时
# 就要求值，写前面会直接 NameError（Python 自上而下执行，这条在 W1 已踩过）。
ManagerDep = Annotated[ConnectionManager, Depends(get_manager)]


def _extract_api_key(websocket: WebSocket) -> str | None:
    """按优先级取出调用方凭据：请求头 → 查询参数。

    为什么必须有第二个来源：**浏览器的 WebSocket API 无法设置自定义请求头**。
    `new WebSocket(url, protocols)` 只有这两个参数，没有 headers 入口。
    这跟我们没法给 EventSource 加 X-API-Key 是同一个原因。

    所以浏览器场景只剩两条路：把凭据放进 URL（简单，但会进服务器访问日志，
    得注意日志脱敏），或者连上之后先发一条鉴权消息（安全，但要自己定协议、
    还得处理"鉴权完成前不许干别的"）。本项目选前者，因为它对客户端最简单。

    注意：这不是"退而求其次"——非浏览器客户端（Python websockets、移动端）
    依然可以正常用请求头，查询参数只是给浏览器兜底。
    """
    header_value = websocket.headers.get("x-api-key")
    if header_value:
        return header_value
    return websocket.query_params.get("api_key")


def _is_authorized(websocket: WebSocket, client_api_key: str) -> bool:
    provided = _extract_api_key(websocket)
    if provided is None:
        return False
    return secrets.compare_digest(provided, client_api_key)


def _validation_error_frame(exc: ValidationError) -> dict[str, Any]:
    """把 Pydantic 的校验错误转成错误帧。

    **WS 没有 422**——状态码是 HTTP 的概念，而这里已经是升级后的 WS 帧了。
    所以错误协议必须自己发明。这里刻意与 REST 的 422 响应体保持同构
    （同样是 loc / type / msg 三个键），客户端才能用一套解析逻辑处理两种协议。

    只挑这三个键是有原因：ValidationError.errors() 里的 ctx 可能带非 JSON
    可序列化的对象，整包塞进 json.dumps 会直接抛 TypeError。
    """
    return {
        "type": "error",
        "reason": "invalid_request",
        "errors": [
            {"loc": list(err["loc"]), "type": err["type"], "msg": err["msg"]}
            for err in exc.errors()
        ],
    }


async def _send_streamed_reply(
    websocket: WebSocket, payload: ChatRequest, settings: Settings
) -> None:
    """在 WS 上逐 token 推送。

    值得和 SSE 对照着看：这里的"流式"**不需要发明任何协议**——直接一条条
    send_json 就行，没有帧格式、没有 data: 前缀、没有空行分隔符、没有
    "多行内容要逐行加前缀"的坑。这正是 WS 相对 SSE 的真实优势所在。

    但反过来，客户端的断线重连、事件序号、心跳也全都没人管了——SSE 那套
    是我们觉得麻烦，WS 那套是我们得亲手写。

    ⚠️ 它不是真流式——传输层是真的，生成层是假的。

    第一行 `reply = build_reply(...)` 的返回类型是 `str`：在发出第一个 token
    之前，整段回复已经生成完毕。逐条 send_json 是真的，"边生成边发"是假的。
    实测（scripts/probe_real_vs_fake_streaming.py，同一份模型成本）：
      现状首字延迟 2.11s  ←→  异步生成器形状 0.30s
    今天看不出来，只因占位模型是瞬间返回的；换成真实模型立刻暴露。

    W3 的改造点就在这里：模型层提供 `AsyncIterator[str]`，本函数改成
    `async for token in stream_reply(...)`——届时不需要 tokenize 来造节奏，
    节奏由模型层自己决定。
    """
    reply = build_reply(payload, settings)
    delay_s = settings.stream_delay_ms / 1000.0

    sent = 0
    async for token in tokenize(reply, delay_s=delay_s):
        sent += 1
        await websocket.send_json({"type": "token", "text": token, "index": sent})

    await websocket.send_json({"type": "done", "chunks": sent})


async def _handle_message(
    websocket: WebSocket, raw: str, settings: Settings, conn: ConnectionManager
) -> None:
    """处理一条客户端消息。

    校验用的是**和 REST 完全相同的 ChatRequest**。这一点很重要：
    "校验放在边界层"不是因为 HTTP 才有边界，而是因为**任何外部输入都是边界**。
    换成 WS、换成 CLI、换成消息队列，同一条规则照样成立，模型也能原样复用。
    """
    try:
        payload = ChatRequest.model_validate_json(raw)
    except ValidationError as exc:
        await websocket.send_json(_validation_error_frame(exc))
        return

    if payload.stream:
        await _send_streamed_reply(websocket, payload, settings)
        return

    started = time.perf_counter()
    reply = build_reply(payload, settings)
    await websocket.send_json(
        {
            "type": "reply",
            "session_id": payload.session_id,
            "reply": reply,
            "model": MODEL_NAME,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    )


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, settings: SettingsDep, conn: ManagerDep) -> None:
    """WS 聊天端点：收一条、回一条；支持 stream=true 时逐 token 推送。

    与 /chat 的两点不同：

    ① **鉴权方式不同**。HTTP 端点用 `Depends(APIKeyHeader)`，框架会在进入路由前
       就拦住。WS 这边手动调用 `_is_authorized`，因为要在 accept 之前决定放不放行。
    ② **拒绝的时机很关键**。必须在 `accept()` **之前** close：这样整个握手就被拒绝，
       浏览器拿到的是"连接失败"；如果先 accept 再 close，客户端会先看到连接建立、
       再收到关闭——客户端代码里"连上了"和"被踢了"变成两件事，很容易写成 bug。
    """
    if not _is_authorized(websocket, settings.client_api_key):
        # 1008 = Policy Violation：语义上正是"你不符合我的策略"
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="缺少或错误的凭据：请用 X-API-Key 请求头或 ?api_key= 查询参数",
        )
        return

    active = await conn.connect(websocket)
    await websocket.send_json(
        {"type": "welcome", "model": MODEL_NAME, "active_connections": active}
    )
    # 广播给"别人"——服务端主动推，不需要任何客户端先发问。
    # 这就是 SSE 做起来别扭、WS 做起来自然的那件事。
    await conn.broadcast({"type": "joined", "active_connections": active}, exclude=websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, raw, settings, conn)
    except WebSocketDisconnect:
        # 客户端断开是【正常路径】，不是异常情况。这里必须捕获，
        # 否则每次用户关页面都会在服务端刷一条 traceback。
        pass
    finally:
        # 用 finally 而不是写在 except 里：无论循环怎么退出（正常断开、
        # 异常、任务被取消），都必须把连接从集合里摘掉——否则集合会持续泄漏，
        # 在线数虚高，广播还会一直往死连接上发。
        remaining = conn.disconnect(websocket)
        await conn.broadcast({"type": "left", "active_connections": remaining})
