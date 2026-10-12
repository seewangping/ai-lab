"""校验服务端日志文件：每一行都是合法 JSON，且访问日志字段齐全。

## 为什么需要这个脚本（而不是再多写几个测试）

Day 6 的验收标准之一是"每个请求一行结构化日志"。这条标准曾经在
**测试全绿**的情况下不成立：测试用例自己调了 setup_logging()，
而应用从不调用，于是真实服务里 ai_lab 的日志全部落到 logging.lastResort——
INFO 被丢掉、WARNING 打成纯文本。

这是个典型的**"测试验证的配置"与"服务运行的配置"不是同一份**的坑。
它无法用测试来防：任何在测试里发生的断言，都可以被"测试自己把配置摆好"绕过去。

唯一能证伪的方式是**读真实服务写出来的那个文件**。所以这个脚本的输入
是一个日志文件路径，而不是一个 app 对象。

## 它校验的是 stdout，不是 stderr

两条流的归属是分开的，这个划分必须说清楚才谈得上"校验"：

  stdout  ← 我们的 JsonFormatter（setup_logging 挂到 root 上，写 sys.stdout）
  stderr  ← uvicorn 自己的 default handler（纯文本、带 traceback）

uvicorn 的日志到不了 root：它的默认配置里 `uvicorn.propagate = False`，
而 `uvicorn.error` 是 `uvicorn` 的子 logger，传播链在那一层被切断。
所以 stdout 是**干净**的——本脚本才能用"每行都必须是 JSON"这种强断言。

⚠️ 唯一的例外是 uvicorn 内置的**访问日志**（`uvicorn.access`）：它自己也写
stdout，而且是纯文本。**必须用 `--no-access-log` 关掉它**，否则这个脚本会
（正确地）报错。实测对照：

    不加 --no-access-log：stdout = 4 行，其中 2 行是
        INFO:     127.0.0.1:60859 - "GET /health HTTP/1.1" 200 OK
    加   --no-access-log：stdout = 10 行，全部是 JSON

关掉它也不亏——我们自己的访问日志信息量严格更大：

    | 字段            | uvicorn.access | ai_lab.middleware |
    |-----------------|----------------|-------------------|
    | request_id      | 无             | 有（12 位十六进制）|
    | duration_ms     | 无             | 有                |
    | 可供日志系统查询 | 不能（纯文本）  | 能（JSON 字段）    |

两条访问日志等于同一件事付两次钱，而且能查询的那条是我们的。

用法（注意两条流要分开重定向）：
    uv run uvicorn ai_lab.api:app --no-access-log > /tmp/app.jsonl 2> /tmp/uvicorn.err &
    uv run python scripts/smoke_api.py
    kill %1
    uv run python scripts/check_json_logs.py /tmp/app.jsonl

⚠️ Windows 上不要把 /tmp/... 直接传给 Python：Git Bash 的 /tmp 映射到
C:\\Users\\<你>\\AppData\\Local\\Temp，而 Windows 版 Python 会把 /tmp 解析成
C:\\tmp —— 于是 FileNotFoundError。用 `cygpath -w /tmp` 取真实路径再传。

退出码：全合规 -> 0，否则 -> 1（可直接接进 CI）。
"""

import json
import re
import sys
from collections import Counter

DEFAULT_LOG = "/tmp/uvicorn.log"

# request_id 的形状由 middleware.py 决定：uuid4().hex 前 12 位
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# 一条访问日志必须具备的字段。名字与 middleware.py 里 extra= 的键一一对应——
# 改名时这里会失败，这正是想要的：字段名是给日志系统看的契约，
# 悄悄改名等于把已有的看板和告警全部打断。
ACCESS_FIELDS = ("request_id", "method", "path", "http_status", "duration_ms")

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  [FAIL] {msg}")


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG

    try:
        with open(path, encoding="utf-8") as fh:
            raw_lines = fh.read().splitlines()
    except FileNotFoundError:
        print(f"日志文件不存在：{path}")
        return 1

    lines = [ln for ln in raw_lines if ln.strip()]
    print(f"日志文件：{path}")
    print(f"非空行数：{len(lines)}")
    print("-" * 78)

    if not lines:
        fail("日志文件是空的 —— 服务什么都没记，或日志没被重定向到这里")
        return 1

    # ① 逐行必须是合法 JSON。
    #    这一条同时守住两件事：格式统一，以及"没有裸 traceback 混进来"
    #    （traceback 在 JSON 里是被转义的字符串；一旦裸着出现，这行就解析不过）。
    records: list[dict[str, object]] = []
    for i, line in enumerate(lines, 1):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"第 {i} 行不是合法 JSON（{exc.msg}）：{line[:100]!r}")
            continue
        if not isinstance(parsed, dict):
            fail(f"第 {i} 行解析出来不是对象：{type(parsed).__name__}")
            continue
        records.append(parsed)

    if len(records) == len(lines):
        ok(f"全部 {len(lines)} 行都是合法 JSON")

    # ② 基础字段（JsonFormatter 的固定四个键）
    bad_base = [r for r in records if not {"time", "level", "logger", "msg"} <= set(r)]
    if bad_base:
        fail(f"{len(bad_base)} 行缺少基础字段（time/level/logger/msg）")
    else:
        ok("每行都带 time/level/logger/msg")

    # ③ 访问日志必须存在。这条是本脚本的核心：
    #    只要中间件那行 INFO 被丢掉，这里立刻变红——而那正是当初的故障现象。
    access = [r for r in records if r.get("msg") == "请求完成"]
    if not access:
        fail('没有任何"请求完成"访问日志 —— 中间件的 INFO 行被丢掉了（检查是否真的装了 JsonFormatter）')
        return 1
    ok(f'访问日志 {len(access)} 条')

    # ④ 访问日志的字段必须齐全，且类型正确。
    #    只检查"键在不在"是不够的：duration_ms 如果被写成字符串，
    #    日志系统里就没法求分位数——那结构化就白做了。
    missing: Counter[str] = Counter()
    for rec in access:
        for field in ACCESS_FIELDS:
            if field not in rec:
                missing[field] += 1
    if missing:
        fail(f"访问日志缺字段：{dict(missing)}")
    else:
        ok(f"访问日志字段齐全：{', '.join(ACCESS_FIELDS)}")

    bad_id = [r.get("request_id") for r in access if not REQUEST_ID_RE.match(str(r.get("request_id")))]
    if bad_id:
        fail(f"request_id 形状不对（应为 12 位十六进制）：{bad_id[:3]}")
    else:
        ok("request_id 形状正确（12 位十六进制）")

    bad_dur = [r.get("duration_ms") for r in access if not isinstance(r.get("duration_ms"), (int, float))]
    if bad_dur:
        fail(f"duration_ms 不是数字，无法做分位统计：{bad_dur[:3]}")
    else:
        ok("duration_ms 是数字（可做分位统计）")

    bad_status = [r.get("http_status") for r in access if not isinstance(r.get("http_status"), int)]
    if bad_status:
        fail(f"http_status 不是整数：{bad_status[:3]}")
    else:
        ok("http_status 是整数")

    # ⑤ 汇总，让人一眼能看到"日志里到底有什么"
    levels = Counter(str(r.get("level")) for r in records)
    print("-" * 78)
    print(f"级别分布：{dict(levels)}")
    print(f"涉及的路径（按出现顺序去重）：")
    seen: list[str] = []
    for rec in access:
        p = str(rec.get("path"))
        if p not in seen:
            seen.append(p)
    print(f"  {seen}")

    print("-" * 78)
    if failures:
        print(f"结果：{len(failures)} 项不符")
        return 1
    print("结果：日志格式全部合规。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
