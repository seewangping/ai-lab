"""用真实 WebSocket 客户端验证 /ws/chat —— 补齐 TestClient 证不了的那部分。

为什么非得有这个东西（Day 4 学到的教训，这里第二次应验）：

    TestClient 不经过真实网络栈。它把 ASGI 事件直接喂给应用，
    协议升级握手、帧编解码、背压、TCP 缓冲全都被跳过了。
    所以「客户端到底收到什么」这件事，只有打真实端口才算数。
    Day 4 的 SSE 是这样（时序全假），WS 也是这样（握手拒绝的表现形式全假）。

跑法（需要服务在跑）：

    uvicorn ai_lab.api:app --port 8000        # 另开一个窗口
    PYTHONPATH=src python scripts/ws_client_demo.py
"""

import asyncio
import json
import sys
import time

from websockets.asyncio.client import connect

from ai_lab.config import get_settings, mask_secret

BASE = "ws://127.0.0.1:8000"
ENDPOINT = f"{BASE}/ws/chat"

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f"\n        └─ {detail}" if detail else ""))


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


async def try_connect(url: str, headers: dict[str, str] | None = None) -> str:
    """尝试握手，返回结果描述。

    成功 → "OK"
    失败 → 描述客户端实际看到的东西（异常类型 + 有没有 HTTP 状态码）
    """
    try:
        async with connect(url, additional_headers=headers or {}):
            return "OK"
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"{type(exc).__name__} (HTTP {status})" if status else type(exc).__name__


async def main() -> int:
    settings = get_settings()
    key = settings.client_api_key

    print(f"目标服务   : {ENDPOINT}")
    print(f"对外凭据   : {mask_secret(key)}   （client_api_key）")
    print(f"上游密钥   : {mask_secret(settings.llm_api_key)}   （llm_api_key —— 不该能用）")

    # ───────────────────────────────────────────── 一、握手阶段就被拒
    section("一、鉴权必须在【握手阶段】就拒绝（这是 WS 独有的机制）")
    print("  说明：接入点如果是 HTTP 端点，响应就是一个 401；")
    print("        WS 没有状态码可用，所以必须在协议升级完成【之前】把连接关掉。")
    print()

    no_cred = await try_connect(ENDPOINT)
    check("不带凭据 → 握手失败", no_cred != "OK", f"客户端看到: {no_cred}")

    wrong = await try_connect(ENDPOINT, {"X-API-Key": "definitely-wrong"})
    check("错凭据 → 握手失败", wrong != "OK", f"客户端看到: {wrong}")

    upstream = await try_connect(ENDPOINT, {"X-API-Key": settings.llm_api_key})
    check(
        "拿【上游 LLM 密钥】当门禁卡 → 握手失败",
        upstream != "OK",
        f"客户端看到: {upstream}",
    )

    # ───────────────────────────────────────────── 二、两种带凭据的方式
    section("二、凭据怎么带（浏览器设不了请求头，所以必须有查询参数这条路）")
    print("  对照实验：")
    print()

    async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as ws:
        welcome = json.loads(await ws.recv())
    check(
        "① 请求头 X-API-Key（Python / 移动端 / 服务端互调都能用）",
        welcome["type"] == "welcome",
        f"welcome.active_connections={welcome['active_connections']}, model={welcome['model']}",
    )

    async with connect(f"{ENDPOINT}?api_key={key}") as ws:
        welcome_q = json.loads(await ws.recv())
    check(
        "② 查询参数 ?api_key=（浏览器唯一可行路径）",
        welcome_q["type"] == "welcome",
        "浏览器的 new WebSocket(url) 没有 headers 参数，只能走这里",
    )

    # ───────────────────────────────────────────── 三、收发与多轮
    section("三、收一条、回一条；一条连接可以聊多轮")
    print()
    async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as ws:
        await ws.recv()  # welcome
        rounds: list[tuple[str, int]] = []
        for index in range(3):
            await ws.send(json.dumps({"message": "补液原则是什么？", "session_id": f"sess-real-{index}"}))
            reply = json.loads(await ws.recv())
            rounds.append((reply["session_id"], reply["latency_ms"]))
    check(
        "连发 3 轮，每轮都拿到对应回复",
        [r[0] for r in rounds] == ["sess-real-0", "sess-real-1", "sess-real-2"],
        f"轮次回复 session_id={[r[0] for r in rounds]}；延迟={[r[1] for r in rounds]} ms",
    )
    print("  注意：这 3 轮共用同一条 TCP 连接——HTTP 的话每轮都要重新建连 + 重新鉴权。")

    # ───────────────────────────────────────────── 四、流式（真实时序）
    section("四、WS 上做流式：不需要发明任何协议")
    print("  这一节的时序是【真的】——因为它走真实网络栈（Day 4 的教训在此应验）。")
    print()
    print("  帧到达时间线（前 6 帧 + 末帧）：")
    arrivals: list[tuple[float, str]] = []
    t0 = time.perf_counter()
    async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as ws:
        await ws.recv()  # welcome
        await ws.send(json.dumps({"message": "补液原则是什么？", "session_id": "sess-stream", "stream": True}))
        while True:
            frame = json.loads(await ws.recv())
            arrivals.append((time.perf_counter() - t0, frame["type"]))
            if frame["type"] == "done":
                done_frame = frame
                break

    for offset, kind in arrivals[:6]:
        print(f"        +{offset:6.3f}s  {kind}")
    if len(arrivals) > 7:
        print(f"        ......（中间 {len(arrivals) - 7} 帧省略）......")
    print(f"        +{arrivals[-1][0]:6.3f}s  {arrivals[-1][1]}")

    token_count = sum(1 for _, k in arrivals if k == "token")
    span = arrivals[-1][0] - arrivals[0][0]
    check("分多帧送达", token_count > 5, f"token 帧 {token_count} 个")
    check(
        "token 是逐帧摊开的（不是一次性到达）",
        span > 0.3,
        f"首帧→末帧间隔 {span:.3f}s，done 帧 chunks={done_frame['chunks']}",
    )

    # ───────────────────────────────────────────── 五、服务端主动推
    section("五、服务端主动推给【其他】客户端（WS 相对 SSE 的真正优势）")
    print("  实验：客户端 A 连上后什么都不发，然后客户端 B 连上——看 A 收到什么。")
    print()
    async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as client_a:
        await client_a.recv()  # welcome

        async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as client_b:
            welcome_b = json.loads(await client_b.recv())
            pushed = json.loads(await client_a.recv())  # A 没发任何东西

    check(
        "A 没发请求，却收到了服务端推来的通知",
        pushed["type"] == "joined",
        f"A 收到 {pushed}（此时 B 的 welcome 说在线数={welcome_b['active_connections']}）",
    )
    print("  这件事用 SSE 做：要么每个客户端各开一条长连接由服务端逐个 push，")
    print("  要么把多路事件复用进一条流、自己定义'这条是给谁的'——复杂度都回到你身上。")

    # ───────────────────────────────────────────── 六、错误协议
    section("六、非法输入：WS 里没有 422，错误协议得自己发明")
    print()
    async with connect(ENDPOINT, additional_headers={"X-API-Key": key}) as ws:
        await ws.recv()  # welcome

        await ws.send("这不是 JSON")
        err_json = json.loads(await ws.recv())

        await ws.send(json.dumps({"message": "", "session_id": "sess-err"}))
        err_empty = json.loads(await ws.recv())

        # 出错之后还能继续聊（HTTP 里 422 之后这个请求就结束了）
        await ws.send(json.dumps({"message": "还能说话吗", "session_id": "sess-err"}))
        after = json.loads(await ws.recv())

    check("非 JSON → 结构化错误帧", err_json.get("reason") == "invalid_request", str(err_json)[:90])
    check(
        "空 message → 与 REST 的 422 同构（loc/type/msg）",
        err_empty["errors"][0]["loc"] == ["message"],
        str(err_empty["errors"][0]),
    )
    check("出错后连接仍可用（WS 不因一次坏输入断开）", after["type"] == "reply", f"拿到 {after['type']}")

    # ───────────────────────────────────────────── 汇总
    print()
    print("=" * 78)
    print(f"结果：{passed} 项符合预期，{failed} 项失败")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    # 连接被拒会抛异常，这里统一兜住并给出可读提示
    try:
        sys.exit(asyncio.run(main()))
    except OSError as exc:
        print(f"连不上 {ENDPOINT}：{exc}")
        print("请先在另一个窗口启动服务：")
        print("    uvicorn ai_lab.api:app --port 8000")
        sys.exit(2)
