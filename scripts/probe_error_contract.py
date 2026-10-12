"""对着【正在运行的服务】验收"统一错误结构"。

为什么不能只靠 pytest：
  TestClient 是进程内直接调 ASGI，它跑不到真实 HTTP 栈。而 Day 6 想验证的
  恰恰有一半在 HTTP 层：状态码、Retry-After 头、X-Request-ID 头、
  "同一个 request_id 既在头里又在体里"。这些用 TestClient 测等于自问自答。

为什么不能只靠 curl：
  curl 能打出一次结果，但打不出【断言】。下一个人改了 handlers.py，
  curl 不会告诉他哪里退化了。这个脚本会，而且退出码能直接接进 CI。

用法（两个终端）：
  终端 A：uv run uvicorn ai_lab.api:app --log-level warning
  终端 B：uv run python scripts/probe_error_contract.py

退出码：全部符合预期 -> 0，任一不符 -> 1。
"""

import json
import sys

import httpx

from ai_lab.config import get_settings

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# 故意混进 unhandled 异常消息里的内容。它们必须出现在【日志】里，绝不能出现在【响应】里。
LEAK_MARKERS = ("sk-fake", "/srv/app", "stub_llm.py", "Traceback", "RuntimeError")

failures: list[str] = []


class Expect:
    """一条期望。字段全列出来，比到处写 if 更容易看出"这一条到底在验什么"。"""

    def __init__(
        self,
        name: str,
        *,
        status: int,
        code: str,
        retryable: bool,
        retry_after: str | None,
        must_not_leak: bool = False,
    ) -> None:
        self.name = name
        self.status = status
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.must_not_leak = must_not_leak


# 七种错误来源。注意 balance 那一行：状态码 503 却没有 Retry-After。
# 这不是疏忽，是设计——"余额不足"不是"稍后重试就能好"，
# 给客户端一个 Retry-After 等于教它白重试。
# 而 upstream 那一条走的是【兜底路径】（LLMServerError 故意没声明 http_status），
# 用来证明 http_status_for() 的兜底分支是活的、不是死代码。
EXPECTATIONS = [
    Expect("rate-limit  上游限流", status=429, code="upstream_rate_limited",
           retryable=True, retry_after="1"),
    Expect("timeout     上游超时", status=504, code="upstream_timeout",
           retryable=True, retry_after="1"),
    Expect("upstream    上游 5xx(走兜底)", status=503, code="upstream_error",
           retryable=True, retry_after="1"),
    Expect("auth        上游鉴权失败(不透传 401)", status=500, code="upstream_auth_failed",
           retryable=False, retry_after=None),
    Expect("bad-request 上游参数错", status=400, code="upstream_invalid_request",
           retryable=False, retry_after=None),
    Expect("balance     余额不足(503 但不可重试)", status=503, code="upstream_insufficient_balance",
           retryable=False, retry_after=None),
    Expect("unhandled   未处理异常(兜底处理器)", status=500, code="internal_error",
           retryable=False, retry_after=None, must_not_leak=True),
]


def ok(name: str, passed: bool, detail: str) -> None:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name:<40} {detail}")
    if not passed:
        failures.append(name)


def probe_one(client: httpx.Client, base: str, exp: Expect) -> None:
    print(f"\n{exp.name}   GET /demo/errors/{exp.name.split()[0]}")
    kind = exp.name.split()[0]
    resp = client.get(f"{base}/demo/errors/{kind}")

    ok("状态码", resp.status_code == exp.status,
       f"http={resp.status_code} 期望={exp.status}")
    ok("Content-Type", resp.headers.get("content-type", "").startswith("application/json"),
       resp.headers.get("content-type", "(无)"))

    try:
        body = resp.json()
    except json.JSONDecodeError:
        ok("响应体是 JSON", False, f"解析失败，前 80 字：{resp.text[:80]!r}")
        return

    ok("error 码", body.get("error") == exp.code,
       f"error={body.get('error')!r} 期望={exp.code!r}")
    ok("retryable", body.get("retryable") is exp.retryable,
       f"retryable={body.get('retryable')!r} 期望={exp.retryable}")
    ok("detail 非空", bool(body.get("detail")), f"{str(body.get('detail'))[:40]}")

    # X-Request-ID 必须【恰好一个】：两侧都写入（handlers 与 middleware）是刻意的，
    # 但要靠 setdefault 去重。如果哪天去重失效，会变成两个同名的头，
    # 客户端拿到的是逗号拼接的字符串，拿它去查日志就查不到了。
    ids = resp.headers.get_list("x-request-id")
    ok("X-Request-ID 恰好一个", len(ids) == 1, f"count={len(ids)} value={ids[:2]}")

    # 体里的 request_id 与头里的必须一致，否则"用户截图里的 id"和
    # "客户端日志里的 id"是两个东西，排障时对不上。
    ok("体/头 request_id 一致", bool(ids) and body.get("request_id") == ids[0],
       f"body={body.get('request_id')} header={ids[0] if ids else None}")

    got_ra = resp.headers.get("retry-after")
    ok("Retry-After", got_ra == exp.retry_after,
       f"值={got_ra!r} 期望={exp.retry_after!r}")

    if exp.must_not_leak:
        raw = resp.text
        hit = [m for m in LEAK_MARKERS if m in raw]
        ok("响应体无内部细节泄露", not hit, f"命中={hit}" if hit else "干净")


def probe_http_layer(client: httpx.Client, base: str, auth: dict[str, str]) -> None:
    """框架自己抛的错误（不是我们 raise 的）也必须穿同一件外套。"""
    print("\n【额外】框架侧错误是否也被统一")

    resp = client.get(f"{base}/no/such/route")
    ok("404 走统一结构", resp.status_code == 404 and "error" in resp.json(),
       f"http={resp.status_code} body={resp.text[:70]}")

    resp = client.delete(f"{base}/health")
    ok("405 走统一结构", resp.status_code == 405 and "error" in resp.json(),
       f"http={resp.status_code} body={resp.text[:70]}")

    # 422 是四类里唯一带 errors 数组的：客户端要靠它逐字段报错。
    # 这里必须带上凭据，否则先撞 401，测不到校验路径——
    # 依赖（API key）比请求体先解析，这是个容易踩的顺序。
    resp = client.post(f"{base}/chat", json={"message": ""}, headers=auth)
    body = resp.json() if resp.status_code == 422 else {}
    n = len(body.get("errors", []))
    ok("422 带 errors 数组", resp.status_code == 422 and n >= 1,
       f"http={resp.status_code} errors={n} 处")
    if n:
        first = body["errors"][0]
        ok("errors 项含 loc/type/msg",
           {"loc", "type", "msg"} <= set(first),
           f"keys={sorted(first)}")

    # 401 必须保住 WWW-Authenticate（RFC 9110）。这条是被回归逼出来的：
    # 换成统一 JSON 之后，deps.py 设的这个头曾经消失过。
    #
    # 这里必须用 POST —— /chat 是 POST 路由。我第一次写成了 GET，拿到 405：
    # 405 是对的（方法不对先于鉴权判定），但它证明不了 401 那条路径。
    # "断言失败先怀疑自己的测试"比"先怀疑代码"省时间。
    resp = client.post(f"{base}/chat", json={"message": "你好"})
    ok("401 缺凭据", resp.status_code == 401, f"http={resp.status_code}")
    ok("401 保住 WWW-Authenticate",
       resp.headers.get("www-authenticate") is not None,
       f"值={resp.headers.get('www-authenticate')!r}")

    # 顺带验一下：带上正确凭据时 401 不该出现（否则上面的断言可能是假阳性）。
    # body 必须完整——session_id 是必填的，漏了会先撞 422（又是一次"先怀疑测试"）。
    resp = client.post(
        f"{base}/chat",
        json={"message": "你好", "session_id": "sess-error-contract"},
        headers=auth,
    )
    ok("带凭据时不报 401", resp.status_code == 200, f"http={resp.status_code}")


def main() -> int:
    settings = get_settings()
    auth = {"X-API-Key": settings.client_api_key}

    print(f"目标服务: {BASE_URL}")
    print("（演示端点不需要 X-API-Key；下面最后一项专门验没有凭据时的 401）")
    print("=" * 82)

    with httpx.Client(timeout=10.0) as client:
        for exp in EXPECTATIONS:
            probe_one(client, BASE_URL, exp)
        print("\n" + "=" * 82)
        probe_http_layer(client, BASE_URL, auth)

    print("\n" + "=" * 82)
    if failures:
        print(f"结果：{len(failures)} 项不符 -> {failures}")
        return 1
    print("结果：全部符合预期。客户端拿到的永远是同一件外套。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
