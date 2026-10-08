"""手工冒烟脚本：对着「正在运行的服务」把关键路径打一遍。

为什么要有它：
  pytest 用的是 TestClient（进程内直接调 ASGI），它测不到真实 HTTP 栈——
  端口绑定、真实请求头、uvicorn 的行为、你自己的终端环境。
  这个脚本补上那一段，是 curl 的"可重复、可断言"版本。

用法（两个终端）：
  终端 A：uv run uvicorn ai_lab.api:app --reload --reload-dir src
  终端 B：uv run python scripts/smoke_api.py

退出码：全部符合预期 → 0，任一不符 → 1（可以接进 CI 或 git hook）。
"""

import json
import sys
import time

import httpx

from ai_lab.config import get_settings, mask_secret

BASE_URL = "http://127.0.0.1:8000"

VALID_BODY = {"message": "糖尿病酮症酸中毒的补液原则是什么？", "session_id": "sess-smoke-001"}

failures: list[str] = []


def check(name: str, actual: int, expected: int, extra: str = "") -> None:
    ok = actual == expected
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name:<44} http={actual} (期望 {expected})"
    if extra:
        line += f"  {extra}"
    print(line)
    if not ok:
        failures.append(name)


def check_true(name: str, ok: bool, extra: str = "") -> None:
    """布尔断言：用于"时序/帧数"这类没有状态码可比的东西。"""
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name:<44} {'符合' if ok else '不符合'}"
    if extra:
        line += f"  {extra}"
    print(line)
    if not ok:
        failures.append(name)


def main() -> int:
    settings = get_settings()
    # 只打印掩码，绝不回显真实密钥（脚本可能被录屏 / 贴进 issue）
    print(f"目标服务: {BASE_URL}")
    print(f"使用密钥: {mask_secret(settings.client_api_key)}   （对外凭据 client_api_key）")
    print("-" * 78)

    auth = {"X-API-Key": settings.client_api_key}

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        # 1. 存活探针：免鉴权
        resp = client.get("/health")
        check("GET /health（免鉴权）", resp.status_code, 200, resp.text)

        # 2. 缺密钥
        resp = client.post("/chat", json=VALID_BODY)
        check("POST /chat 缺 X-API-Key", resp.status_code, 401, resp.text)

        # 3. 错密钥
        resp = client.post("/chat", json=VALID_BODY, headers={"X-API-Key": "sk-wrong"})
        check("POST /chat 错 X-API-Key", resp.status_code, 401)

        # 4. 未授权 + 非法体：应当被鉴权先挡住（401，而不是 422）
        resp = client.post("/chat", json={"garbage": True})
        check("POST /chat 未授权且体非法", resp.status_code, 401, "鉴权先于体校验")

        # 5. 合法请求
        resp = client.post("/chat", json=VALID_BODY, headers=auth)
        check("POST /chat 合法请求", resp.status_code, 200)
        if resp.status_code == 200:
            body = resp.json()
            print(f"        └─ reply      : {body['reply']}")
            print(f"        └─ 字段集合   : {sorted(body)}")
            leaked = {"_internal_trace_id", "_raw_prompt"} & set(body)
            print(f"        └─ 内部字段泄漏: {leaked or '无（response_model 已过滤）'}")

        # 6. 已授权但体非法 → 422，且能拿到字段级定位
        resp = client.post("/chat", json={"message": "", "session_id": "sess-x"}, headers=auth)
        check("POST /chat 空 message", resp.status_code, 422)
        if resp.status_code == 422:
            first = resp.json()["detail"][0]
            print(f"        └─ 首个错误   : loc={first['loc']} type={first['type']}")

        # 7. 历史端点（受保护）
        resp = client.get("/chat/history/sess-smoke-001", params={"limit": 5}, headers=auth)
        check("GET /chat/history/{id} 带密钥", resp.status_code, 200, resp.text)

        # 8~11. SSE 流式：内容维度 + 时序维度（时序只能在真实 HTTP 上验）
        started = time.perf_counter()
        first_at: float | None = None
        last_at: float | None = None
        data_frames = 0
        events: list[str] = []
        current_event: str | None = None
        done_payload: dict[str, object] = {}

        with client.stream("POST", "/chat/stream", json=VALID_BODY, headers=auth) as resp:
            check(
                "POST /chat/stream 流式响应",
                resp.status_code,
                200,
                f"content-type={resp.headers.get('content-type')}",
            )
            check_true(
                "响应头 X-Accel-Buffering: no",
                resp.headers.get("x-accel-buffering") == "no",
                "（缺了它 Nginx 会把 token 攒起来）",
            )
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    current_event = line.removeprefix("event: ")
                    events.append(current_event)
                elif line.startswith("data:"):
                    now = time.perf_counter() - started
                    if first_at is None:
                        first_at = now
                    last_at = now
                    data_frames += 1
                    if current_event == "done":
                        done_payload = json.loads(line.removeprefix("data:").strip())

        check_true(
            "SSE 帧数 > 10（真的分了多帧）",
            data_frames > 10,
            f"data 帧 {data_frames} 个",
        )
        check_true(
            "事件序列 meta → token × N → done",
            events[:1] == ["meta"] and events[-1:] == ["done"] and "token" in events,
            f"首 {events[:1]} 末 {events[-1:]}",
        )
        check_true(
            "首帧到达 < 0.3s（没有被缓冲）",
            first_at is not None and first_at < 0.3,
            f"首帧 {0 if first_at is None else first_at:.3f}s",
        )
        check_true(
            "首末帧间隔 > 0.3s（token 是逐帧摊开的）",
            first_at is not None and last_at is not None and (last_at - first_at) > 0.3,
            f"间隔 {(last_at or 0) - (first_at or 0):.3f}s",
        )
        if done_payload:
            print(f"        └─ done 帧   : chunks={done_payload.get('chunks')} "
                  f"ttft_ms={done_payload.get('ttft_ms')} latency_ms={done_payload.get('latency_ms')}")

        # 12. stream=true 走同一个端点也应返回 SSE
        with client.stream("POST", "/chat", json={**VALID_BODY, "stream": True}, headers=auth) as resp:
            check(
                "POST /chat stream=true 返回 SSE",
                resp.status_code,
                200,
                f"content-type={resp.headers.get('content-type')}",
            )

    print("-" * 78)
    if failures:
        print(f"结果：{len(failures)} 项不符合预期 → {failures}")
        return 1
    print("结果：全部符合预期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
