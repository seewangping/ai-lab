# ai-lab

一个用于练习 **Python 后端工程化** 的最小项目：把「配置 / 日志 / 异常 / 异步并发 / 测试 / 容器化」这六件事按生产标准各做一遍，作为后续做 AI 应用（LLM 服务）的地基。

目标不是功能多，而是**每条工程约束都有可运行的证据**：类型检查零报错、测试覆盖率 100%、镜像 197MB、容器以非 root 运行。

第 1 周打地基（配置 / 日志 / 异常 / 异步 / 测试 / 容器化），第 2 周在此基础上长成一个 FastAPI 服务：入参校验、依赖注入、SSE 流式输出。

## 目录结构

```
ai-lab/
├── src/ai_lab/
│   ├── __init__.py         # main()：最小可运行入口
│   ├── __main__.py         # python -m ai_lab 的入口（容器 CMD 走这条路径）
│   ├── config.py           # 配置层：pydantic-settings 读 .env，fail fast + lru_cache 单例
│   ├── logging_setup.py    # 结构化日志：单行 JSON，幂等初始化
│   ├── errors.py           # 异常体系：可重试 / 不可重试分类 + HTTP 状态码映射
│   ├── session.py          # 数据类练习（含校验逻辑）
│   ├── async_demo.py       # 并发实验脚本：gather vs 串行、超时、信号量限流
│   ├── api.py              # [W2] FastAPI 应用：/health /greet /chat /chat/stream /chat/history
│   ├── schemas.py          # [W2] Pydantic 请求/响应模型（校验规则写在边界层）
│   ├── deps.py             # [W2] 依赖注入：Settings 提供者 + X-API-Key 鉴权（APIKeyHeader）
│   ├── streaming.py        # [W2] SSE 帧编码、心跳、异步生成器（手写协议，不引库）
│   ├── ws.py               # [W2] WebSocket 端点 + 连接管理器（与 SSE 的取舍见下）
│   └── stub_llm.py         # [W2] 占位模型层：接真实 LLM 时唯一要替换的模块
├── tests/                  # pytest 用例，不依赖本机真实 .env
├── scripts/                # 手动验证脚本：冒烟测试、时序探针、Python 语法导览
├── docs/                   # 读书笔记 / 速查表
├── Dockerfile              # 多阶段构建，运行阶段不含 uv / pytest / 编译器
├── compose.yaml            # 开发用：挂载源码 + 注入 .env
├── .env.example            # 配置模板（提交）；.env 是真实值（不提交）
├── .dockerignore
└── pyproject.toml          # 依赖 + mypy / coverage 配置
```

## 本地运行

```bash
cp .env.example .env      # 然后把两把钥匙改成你自己的值（必须是两个不同的值）
uv sync                   # 建虚拟环境并装依赖（需要已安装 uv）
uv run python -m ai_lab   # 冒烟：W1 留下的最小入口，只打印一行，用来确认包能导入
```

起服务（`--no-access-log` 不是可选项，理由见「服务端日志」一节）：

```bash
# 开发（改代码自动重载）
uv run uvicorn ai_lab.api:app --reload --reload-dir src

# 想看结构化日志时：两条流分开重定向，stdout 就是干净的 JSON 日志
uv run uvicorn ai_lab.api:app --no-access-log > /tmp/app.jsonl 2> /tmp/uvicorn.err
```

交互式接口文档在 <http://127.0.0.1:8000/docs>（`/ws/chat` 不会出现在里面，WS 不进 OpenAPI）。

`.env` 里有两把**职责不同、必须不同**的钥匙：

| 变量 | 用途 | 谁看得到 |
|---|---|---|
| `CLIENT_API_KEY` | 调用方发在 `X-API-Key` 头里的凭据 | **发给别人**，按「会被泄露」设计：可轮换、可吊销 |
| `LLM_API_KEY` | 服务端调上游 LLM 用 | 只留在这台机器，绝不外发、绝不回显 |

两把填成同一个值会被 `config.py` 的校验**拒绝启动**——拆开的意义全在「失效域不同」：
对外密钥泄露只需吊销重发，上游密钥泄露动的是真金白银的账号。

想看到当前生效的配置（两把钥匙都会脱敏）：`uv run python src/ai_lab/config.py`

## 质量门

```bash
uv run mypy src/ tests/
uv run pytest -q --cov=ai_lab --cov-report=term-missing
```

当前状态：`mypy` 27 个文件零报错（scripts 另 9 个，需 `MYPYPATH=src`）；`pytest` **150** 个用例全过，
覆盖率 **100%**（492 条语句）。CI 在 `ubuntu-latest` 上跑 Python 3.12 与 3.13 两遍。

端到端另有一层手动验证（需要服务在跑，**建议按下面这条命令起**，理由见「服务端日志」一节）：

```bash
# 关键：--no-access-log 关掉 uvicorn 内置的纯文本访问日志；两条流分开重定向
uv run uvicorn ai_lab.api:app --port 8000 --no-access-log > /tmp/app.jsonl 2> /tmp/uvicorn.err
```

```bash
uv run python scripts/smoke_api.py              # HTTP：15 项冒烟（鉴权/校验/流式/响应头）
uv run python scripts/ws_client_demo.py         # WS：12 项真实客户端验证（握手拒绝/广播/双向）
uv run python scripts/probe_error_contract.py   # 错误契约：64 项断言（7 种错误源 + 框架侧错误）
uv run python scripts/check_json_logs.py /tmp/app.jsonl   # 日志格式：每行必须合法 JSON 且字段齐全
uv run python scripts/probe_stream_timing.py    # 时序对照：TestClient 全是 0.000s，真实 HTTP 才有间隔
uv run python scripts/probe_sleep_granularity.py  # 睡眠精度：不需要服务，可直接单独跑
```

> 前 5 条都需要服务在跑（`probe_stream_timing.py` 尤其需要——它就是用来揭穿
> TestClient 的时序假象的）；只有最后一条 `probe_sleep_granularity.py` 是纯本地测量。

> **为什么这几层缺一不可**：TestClient 不经过真实网络栈——协议升级、帧编解码、
> 缓冲全被跳过。「被拒绝的握手，客户端到底看到什么」这类问题它答不了。
> 实测：TestClient 里握手被拒是 `WebSocketDisconnect`，真实客户端拿到的是
> **HTTP 403**（`InvalidStatus`）——同一个行为，两种表现。Day 4 的 SSE 时序也有同样的坑。
>
> 而 `check_json_logs.py` 保护的是**比协议更靠外的一层**：它读的是真实服务写出的
> 那个文件。有些问题在测试里根本无法证伪，因为测试可以自己把环境摆好——
> 详见「服务端日志：一个测试永远发现不了的坑」。

### 在 Git Bash 终端里怎么跑、怎么看结果

**先记住一个坑：不要用裸 `python`。** 这台机器上 `python` 解析到的是 WorkBuddy 的
受管解释器（`~/.workbuddy/binaries/python/...`），**不是本项目 venv**，实测直接报
`No module named pytest`、退出码 1。两种正确写法：

```bash
cd /d/ai-lab
uv run pytest -q                              # 方式一：uv 自动找到 .venv（推荐，最短）
./.venv/Scripts/python.exe -m pytest -q       # 方式二：显式指定（不依赖 uv）

# 覆盖率（12 个源文件全 100%）
uv run pytest -q --cov=ai_lab --cov-report=term-missing
uv run pytest -q --cov=ai_lab --cov-report=html   # 另外生成 htmlcov/index.html

# 类型检查
uv run mypy src/ tests/                       # 项目标准门
MYPYPATH=src uv run mypy scripts/             # 脚本目录单跑需要 MYPYPATH
```

**日常筛选**（都实测过）：

```bash
uv run pytest tests/test_ws.py -q             # 只跑一个文件 → 19 passed
uv run pytest tests/test_ws.py -v             # 逐条列出用例名与进度
uv run pytest -x -q                           # 遇到第一个失败就停
uv run pytest --lf -q                         # 只重跑上次失败的用例
uv run pytest tests/test_ws.py -q -k "key or credential"   # 按用例名过滤 → 5 passed
```

> `-k` 匹配的是**用例名**，不是文件内容。写了个匹配不上的词（如 `-k "auth"`，
> 而 `test_ws.py` 里没有用例名含 `auth`）会得到 `19 deselected`，**退出码 5**——
> 看起来像"测了但没输出"，其实是一条都没跑。

**退出码语义**（要写进脚本时用得上，实测确认）：

| 退出码 | 含义 |
|---|---|
| `0` | 全部通过 |
| `1` | 有用例失败（含收集期报错） |
| `5` | 一条都没被收集/选中 |
| `2` | 被中断（Ctrl+C） |

**⚠️ 取退出码时中间不能插任何命令**，否则拿到的是别的命令的退出码：

```bash
uv run pytest -q > /tmp/t.log 2>&1    # 正确：重定向到文件，不用管道
echo "退出码=$?"
tail -3 /tmp/t.log                    # 再看输出

uv run pytest -q | tail -3            # ❌ 退出码来自 tail，永远是 0
```

> 这类"假报成功"在本项目已经踩过两次：一次是 `git push | tail` 把 502 报成成功，
> 一次是 `PIPESTATUS` 中间插了 `echo` 被冲掉。

## 端点

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/health` | 免 | 存活探针 |
| GET | `/greet/{name}` | 免 | 最小示例（路径参数 + 查询参数） |
| POST | `/chat` | ✅ | 对话；`stream=true` 时返回 SSE |
| POST | `/chat/stream` | ✅ | 专用 SSE 流式端点 |
| GET | `/chat/history/{session_id}` | ✅ | 路径/查询参数约束示例 |
| **WS** | `/ws/chat` | ✅ | WebSocket：多轮对话、`stream=true` 逐 token、服务端广播 |

⚠️ **`/ws/chat` 不会出现在 `/docs` 里**——OpenAPI 规范只描述 HTTP 请求/响应，
WebSocket 不属于它。所以 WS 端点必须**手工写文档**（就是下面这段），
这不是配置没调好，是规范本身的边界。

### curl 示例（可直接复制）

```bash
# 从 .env 取出对外凭据。注意拿的是 CLIENT_API_KEY（对外），不是 LLM_API_KEY（上游）
KEY=$(grep '^CLIENT_API_KEY=' .env | cut -d= -f2)

# 存活探针（免鉴权）
curl -s http://127.0.0.1:8000/health
# {"status":"ok"}

# 路径参数 + 查询参数
curl -s "http://127.0.0.1:8000/greet/wang?excited=true"

# 对话（非流式）
curl -s -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"message":"你好","session_id":"sess-1"}'

# 对话（流式）。-N 必须有——不加的话你看到的是 curl 在缓冲，不是服务端的问题
curl -sN -X POST http://127.0.0.1:8000/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"message":"你好","session_id":"sess-1"}'

# 故意漏掉凭据，看统一错误外壳（-i 连响应头一起看）
curl -si -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"x","session_id":"s"}' | head -14

# WebSocket 不进 OpenAPI，只能用真实客户端测
uv run python scripts/ws_client_demo.py
```

`session_id` 是必填的（有格式约束），漏了会拿到 422——正好可以顺手看一眼
`errors` 数组是怎么逐字段定位的。

## SSE vs WebSocket：为什么聊天回复选 SSE

这是 Day 5 的验收题，答案是三条**具体的工程理由**，没有一条是"哪个新"：

| | SSE（`/chat/stream`） | WebSocket（`/ws/chat`） |
|---|---|---|
| 方向 | 服务端 → 客户端（单向） | 双向 |
| 底层 | **就是一个 HTTP 响应** | 先协议升级（101），之后不再有 HTTP 语义 |
| 断线重连 | 浏览器 `EventSource` 内置，自动带 `Last-Event-ID` 续传 | 自己写重连 + 自己定义"从哪继续" |
| 认证 | 直接复用现成机制（cookie / 中间件 / 请求头） | 升级后要单独处理（见下） |
| 中间设施 | 状态码、CORS、HTTP/2 多路复用、Nginx 规则**全部照旧** | 网关要单独为它开口子（超时、缓冲、代理配置都得另配） |
| 心跳/序号 | 协议里没有，得自己约定（本项目用 `event: done` + 注释帧心跳） | 协议层有 ping/pong，但业务序号仍得自己加 |
| 自动文档 | 出现在 `/docs` 里 | **不出现在 `/docs` 里**，要手写 |
| 成本 | 每次对话一个新请求 | 建连一次、多轮复用 |
| 服务端主动推给**多个**客户端 | 别扭 | 自然（本项目 `joined`/`left` 广播就是它） |

**三条理由**：

1. **聊天是单向流**。客户端发一条、服务端流式回一段——SSE 天生就是这个形状。
   WS 的双向能力在这个场景里是闲置的，为用不上的能力付复杂度不划算。
2. **SSE 白送一整套 HTTP 设施**。因为它的响应就是一个普通 HTTP 响应，
   认证中间件、CORS、限流、网关转发规则全都不用改；WS 升级之后这些全部失效，
   要一个个单独配。
3. **重连是别人的问题**。SSE 的断线重连由浏览器实现并自动续传；
   WS 的重连、去重、断点续传全得自己写。

**WS 该上的场景**：客户端要持续往服务端推数据（协同编辑、游戏操作流），
或服务端要主动推给多个客户端（在线状态、通知）——后者在 `ws.py` 里有完整演示。

### 一个两边都会撞上的坑：浏览器的凭据怎么带

`EventSource` 和 `WebSocket` **都无法设置自定义请求头**——
`new WebSocket(url, protocols)` 和 `new EventSource(url)` 都没有 headers 参数。

所以 `X-API-Key` 这种自定义头在浏览器里用不了，只剩两条路：

| 方案 | 优点 | 代价 |
|---|---|---|
| 凭据放进 URL（`?api_key=`，本项目 WS 采用） | 客户端最简单 | **会进服务器访问日志**，必须做日志脱敏；URL 可能被分享/留存 |
| 连上后先发一条鉴权消息 | 不进日志 | 要自己定协议，还要处理"鉴权完成前不许干别的"的中间态 |
| 换成 Cookie（SSE 可用） | 浏览器自动携带 | 需要 CSRF 防护；跨域下更麻烦 |

非浏览器客户端（Python `websockets`、移动端、服务间互调）不受这个限制，
`X-API-Key` 正常可用——所以 `ws.py` 里两个来源都支持，请求头优先。
这条限制是本项目把凭据拆成 `CLIENT_API_KEY`（可轮换）而不是直接用上游密钥的又一个理由。

## 统一错误契约（Day 6）

目标只有一句：**客户端永远拿到同一种 JSON 外壳，永远不会看到裸 traceback。**
麻烦在于异常会从四个彼此不相干的地方冒出来，而它们本该长得一样：

| 来源 | 谁抛的 | 处理器 | 典型状态码 |
|---|---|---|---|
| ① 业务异常 | 我们的 `AppError` 子类（W3 接 LLM 后是主要来源） | `app_error_handler` | 由异常类决定 |
| ② 请求体校验 | FastAPI 的 `RequestValidationError` | `validation_error_handler` | 422 |
| ③ HTTP 层 | 框架/路由抛的 `StarletteHTTPException`（404、405、401） | `http_exception_handler` | 原样透出 |
| ④ 谁都没接住 | 任何 bug（`KeyError`、`TypeError`……） | `unhandled_error_handler` | 500 |

外壳长这样（四个来源**完全同构**）：

```json
{
  "error": "upstream_rate_limited",
  "detail": "上游限流",
  "retryable": true,
  "request_id": "bbfbd986b541"
}
```

`errors` 数组只在 422 时出现（客户端要靠它逐字段报错），结构与 WS 错误帧**刻意同构**
（同样是 `loc`/`type`/`msg`），这样客户端能用一套解析逻辑处理两种协议。

### 状态码怎么定：类上显式写 > 按可重试兜底

```python
def http_status_for(exc: AppError) -> int:
    if exc.http_status is not None:
        return exc.http_status
    return 503 if exc.retryable else 500
```

`LLMServerError` **故意不声明** `http_status`，用来让兜底分支成为活代码——否则
"兜底逻辑"只是没人走过的死分支。`scripts/probe_error_contract.py` 里
`upstream` 那一条就是专门验证它的（断言 503）。

两个不显然的决策：

- **`LLMAuthError` 返回 500，而不是把上游的 401 透传给客户端。** 上游 401 的意思是
  "服务端自己的密钥不对"——这是**我们的配置故障**，不是调用方的问题。透传 401 会让
  调用方以为是自己没带凭据，然后去改一个根本没问题的 header。
- **`LLMInsufficientBalanceError` 是 503，但没有 `Retry-After`。** 状态码说"暂时不可用"，
  而"余额不足"不会因为等一会儿就好了。给它 `Retry-After` 等于教客户端白重试。
  实测输出：`rate-limit/timeout/upstream` 三个有 `Retry-After: 1`，
  `balance/auth/bad-request/unhandled` 四个没有。

### 三个"被测试逼出来"的细节

写统一响应最容易丢的是**响应头**，这三条都是先出问题后补的：

1. **401 的 `WWW-Authenticate` 必须保住**。换成自定义 JSON 响应后 `deps.py` 设的
   `WWW-Authenticate: ApiKey` 消失了——RFC 9110 要求 401 必须带它，丢了它不只是少个
   字段，而是让"客户端该怎么带凭据"失去标准表达（浏览器弹密码框、部分 SDK 的自动重试
   都靠它）。修法：`_json_error` 把 `exc.headers` 原样带上。
2. **`X-Request-ID` 要两侧都写**。未处理异常的 500 由最外层的 `ServerErrorMiddleware`
   直接发出，**根本不经过我们的中间件**，所以那一条响应上中间件没机会加头——而它恰恰
   是最需要拿 request_id 去查日志的那一条。修法：`_json_error` 也写，
   中间件侧 `setdefault` 去重，并加回归测试断言 `X-Request-ID` **恰好一个**
   （两个同名头会被客户端拼成逗号串，拿着它查不到日志）。
3. **422 的 `errors` 只能保留 `loc`/`type`/`msg`**。`exc.errors()` 里的 `ctx` 可能含
   不可 JSON 序列化的对象，整包塞进 `JSONResponse` 会直接抛 `TypeError`——
   "想报 422"变成"报 500"。

### HTTPException 还是自定义异常？

判断标准一句话：**这个错误在"没有 HTTP"的世界里还有意义吗？**

- 有 → `AppError`。`errors.py` 里连 `import fastapi` 都没有，状态码只是普通 int，
  所以同一套异常在 CLI、消息队列消费者、批处理里都能用；只有"暴露成 HTTP"时才需要
  一个翻译器（就是 `handlers.py`）。
- 没有 → `HTTPException`。404 路由不存在、405 方法不对，这些概念在非 HTTP 场景里
  根本不存在，硬造成 `AppError` 是给自己加翻译负担。

注册时注意**要注册 Starlette 的 `HTTPException` 父类**：路由没匹配上时框架抛的是父类，
只注册 FastAPI 那个子类的话 404 会绕过我们，长回默认的 `{"detail": "Not Found"}`。

## 请求上下文中间件（Day 6）

`RequestContextMiddleware` 干两件事：给每个请求生成 `request_id` 放进
`scope["state"]`，以及**每个请求输出恰好一行**结构化访问日志：

```json
{"time": "2026-10-12T07:11:02.950338+00:00", "level": "INFO", "logger": "ai_lab.middleware",
 "msg": "请求完成", "request_id": "be7d3fc9ce3a", "method": "GET", "path": "/health",
 "http_status": 200, "duration_ms": 34.786}
```

字段是**独立的键**而不是拼进消息字符串——这是"结构化日志"四个字的实际含义：
`path` 可以直接分组、`duration_ms` 可以直接求分位线。拼进 msg 就只能用眼睛看。

级别按状态码分：5xx 用 `ERROR`，其余用 `INFO`。**这个区分直接决定告警有没有用**——
把 400 也打成 error，报警群很快就被"正常营业噪音"淹掉，真故障反而没人看。

### 为什么是纯 ASGI 中间件，而不是 `BaseHTTPMiddleware`

教科书说法是"`BaseHTTPMiddleware`（`@app.middleware("http")`）会破坏流式响应的时序"。
本项目**实测了一遍**（`scripts/probe_middleware_vs_sse.py`，自动起服务、带预热排除冷启动、
只统计 token→token 间隔）：

| 写法 | token 帧 | 总帧数 | 首字延迟 | 总时长 |
|---|---|---|---|---|
| 无中间件（基线） | 66 | 68 | ~0.12–0.14s | 4.16–4.19s |
| 纯 ASGI 中间件 | 66 | 68 | ~0.12–0.14s | 4.16–4.19s |
| `BaseHTTPMiddleware` | 66 | 68 | ~0.12–0.14s | 4.16–4.19s |

**在 starlette 1.7.0 上三者没有可观测差异**——这个结论与该说法的流传版本相反，
所以脚本里如实写了这一点，而不是把流行说法抄成结论。

那为什么还是选纯 ASGI？理由变成"职责清晰、少一层不可见抽象"，而不是"为了性能"：
`BaseHTTPMiddleware` 内部要起 anyio 任务组、包一层 `_StreamingResponse`、
对 `request` 对象做重包装，这些在你的调用栈里都看不见，出问题时要多扒两层才能定位。
**以性能为名做选择时，先把性能量出来；量不出来就换个诚实的理由。**

顺带一提，基线（"无中间件"）第一次跑出来是最慢的（TTFT 0.905s）——那是**冷启动被
错误归因**了。加一次丢弃结果的预热调用后基线降到 0.120s。这类"测出来的结论其实是
测量方法的产物"，在写性能对比时是最常见的坑。

## 服务端日志：一个测试永远发现不了的坑

**这里的结论是实测出来的，而且测试全绿时它就存在。**

`setup_logging()` 原先只在 `logging_setup.py` 自己的 `__main__` 块和各测试用例里
被调用——**应用自己从不调用**。于是真实服务里：

- `ai_lab.*` 的日志落到 `logging.lastResort`（只在"整条继承链上一个 handler 都没有"时
  生效，恰好是这种情况）。它的行为是：**只处理 WARNING 及以上、纯文本、写往 stderr**。
  - 中间件的 INFO 访问日志 → **整条被丢弃**。"每个请求一行日志"当场不成立。
  - 400 那类 WARNING → 打出来了，但**没有 JSON 外壳**，纯文本，JSON 采集器必然解析失败。
- uvicorn 自己的日志照旧正常（`uvicorn.error` 的传播链在 `uvicorn.propagate = False`
  处被切断，到不了 root）。**这恰恰掩盖了问题**——日志里"看着有东西"。

为什么整套测试都没发现：**测试自己调了 `setup_logging()`。**测试验的是"某份配置是对的"，
服务跑的是"另一份配置"。这类问题无法用测试证伪，因为任何断言都可以被"测试自己把环境
摆好"绕过去。只有读**真实服务写出来的那个文件**才行——所以有了 `scripts/check_json_logs.py`，
以及 CI 里那一项 `logs=`。

修法是用 `lifespan`（而不是模块顶层）调用 `setup_logging()`，三个理由：
① 避免导入副作用——`import` 一个模块不该改全局日志配置；
② 时机要晚于 uvicorn 的 `dictConfig`；
③ 这是唯一能让"每请求一行"在真实服务里成立的地方。

### 两条流是分开的，要分开重定向

| 流 | 谁写 | 格式 | 归属 |
|---|---|---|---|
| **stdout** | 我们的 `JsonFormatter` | JSON | 采集端只需要管这一条 |
| **stderr** | uvicorn 的 `default` handler | 纯文本 + traceback | 框架自己的事 |

实测一次 `/demo/errors/unhandled`：stdout 10 行（其中 ERROR 只有 2 条：中间件 1 + 异常
处理器 1），stderr 82 行纯文本。**干净分离，没有互相污染。**

⚠️ 唯一的例外是 uvicorn 内置的**访问日志**，它自己也写 stdout 且是纯文本。实测对照：

```bash
# 不加 --no-access-log → stdout 里一半的行不可解析
INFO:     127.0.0.1:60859 - "GET /health HTTP/1.1" 200 OK
{"time": "...", "msg": "请求完成", "request_id": "be7d3fc9ce3a", ...}

# 加 --no-access-log  → 全部是 JSON
```

关掉它也不亏，我们自己的那条信息量严格更大：

| 字段 | `uvicorn.access` | `ai_lab.middleware` |
|---|---|---|
| `request_id` | 无 | 有（12 位十六进制，可去日志里捞 traceback） |
| `duration_ms` | 无 | 有（可求分位线） |
| 能否被日志系统查询 | 不能（纯文本） | 能（JSON 字段） |

两条访问日志等于同一件事付两次钱，而且能查询的那条是我们的。

### 一次未处理异常会有 3 份 traceback

中间件 1 份（带 `duration_ms` + `request_id` 的"请求视角"）、异常处理器 1 份
（带 `error` 码的"语义视角"）、uvicorn 1 份（框架兜底，**关不掉**）。
我们只能控制前两份，而它们信息侧重不同，所以都保留。这个代价要知道，不用试图消除。

> **⚠️ 顺手记一个 Windows 上的坑**：Git Bash 的 `/tmp` 映射到
> `C:\Users\<你>\AppData\Local\Temp`，但 Windows 版 Python 会把 `/tmp/x.json`
> 解析成 `C:\tmp\x.json` → `FileNotFoundError`。跨工具传路径时先 `cygpath -w /tmp`
> 拿真实路径。（这个坑让一次 curl 巡检的 `code=` 列全空，排查了一阵才发现是路径问题。）

## 补充材料（写给 C# 出身的自己）

- `docs/python-syntax-for-csharp-devs.md` —— **读码五步法** + C#↔Python 语法对照表 + 本项目真实代码的逐行解码
- `docs/sse-streaming-pitfalls.md` —— **SSE 流式响应的 8 个坑**。每条都是本项目实测的，包括
  「`X-Accel-Buffering` 到底在哪种部署形态下才起作用」「SSE 协议里为什么没有结束标记」这两个
  容易只停留在「听说过」层面的问题。也记了一个**被实测否掉的流行说法**（`BaseHTTPMiddleware` 破坏流式）
- `scripts/syntax_tour.py` —— 13 节可运行的语法导览，每节都有 C# 对照和打印出来的证据
- `scripts/probe_sleep_granularity.py` —— 量 `asyncio.sleep` 与 `time.sleep` 的实际精度，
  并**反推**平台粒度（而不是硬编码 15.6ms）

```bash
uv run python scripts/syntax_tour.py        # 跑全部
uv run python scripts/syntax_tour.py 4 8    # 只看推导式和装饰器
uv run python scripts/probe_sleep_granularity.py   # 不需要起服务，可直接跑
```

## Docker 运行

```bash
docker build -t ai-lab:dev .
docker run --rm -p 8000:8000 --env-file .env ai-lab:dev
```

开发期改代码不用重新构建（compose 挂载了 `./src`）：

```bash
docker compose run --rm app
```

镜像设计要点：

- **多阶段构建**：运行阶段只有 `python:3.13-slim` + `/app`，不含 uv、pytest、mypy、编译器
- **非 root 运行**：以 `uid=1000(appuser)` 启动
- **密钥不进镜像**：`.env` 通过 `--env-file` / `env_file` 在运行时注入，镜像里不存在该文件
- **依赖层可长期缓存**：`pyproject.toml` + `uv.lock` 先于源码被 COPY，改代码不会触发重新装依赖
- **`HEALTHCHECK`**：用 `/health` 做存活探针。slim 镜像里没有 curl，所以用 Python 标准库发请求，不为此引入依赖
- **启动命令写死 `--no-access-log`**：见下方「启动命令的两个必写参数」

### 启动命令的两个必写参数

这两条都是实测出来的，缺一个都会以「本地好好的、容器里不对」的形式出现：

```dockerfile
CMD ["uvicorn", "ai_lab.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
```

| 参数 | 不写会怎样 | 为什么 |
|---|---|---|
| `--host 0.0.0.0` | `-p 8000:8000` 映射正确也连不上 | uvicorn 默认只监听 `127.0.0.1`（回环）。容器外访问不到，而镜像本身「运行正常」，很容易误判成防火墙问题 |
| `--no-access-log` | stdout 里一半的行不是合法 JSON | uvicorn 内置访问日志也写 stdout 且是**纯文本**。我们中间件那条信息量严格更大（带 `request_id` + `duration_ms`），留着框架那条等于同一件事记两遍、还毁掉日志格式 |

实测证据（容器起来后 `docker logs` 的两条流分开）：

```
stdout 9 行  → 全部合法 JSON，check_json_logs.py 退出码 0
stderr 4 行  → 全部是 uvicorn 自己的启动消息（纯文本）
              INFO:     Uvicorn running on http://0.0.0.0:8000   ← 证明 --host 生效
```

stderr 里**没有**访问日志行，stdout 里**没有**纯文本行——两条流各归各位。

### 怎么复核上面这些说法

不必信文档，下面这些命令都可以直接跑：

```bash
# 起容器并等就绪
docker run -d --name ailab-test -p 8020:8000 \
  -e CLIENT_API_KEY=test-client -e LLM_API_KEY=test-upstream ai-lab:dev

# 证明了 --host 0.0.0.0 与端口映射都对
curl -s http://127.0.0.1:8020/health

# 证明运行身份非 root（应输出 uid=1000(appuser)）
docker exec ailab-test id

# 证明镜像里没有开发工具
docker exec ailab-test sh -c 'command -v uv pytest mypy || echo 都没装'

# 证明密钥没进镜像。注意要精确匹配：grep -i env 会被 .venv 匹配到（venv 含 "env"）
docker run --rm ai-lab:dev sh -c 'ls -a /app | grep -x "\.env" || echo 镜像内无 .env'

# 证明 stdout 是纯 JSON（把两条流分开取再校验）
docker logs ailab-test 1>stdout.txt 2>stderr.txt
uv run python scripts/check_json_logs.py "$(cygpath -w stdout.txt)"

docker rm -f ailab-test
```

### 构建耗时（本机实测）

| 场景 | 耗时 | 说明 |
|---|---|---|
| 零改动重构建 | **17s**（15/17/18，中位 17） | 全部层命中缓存，这就是本机的固定开销 |
| 改一行源码后重构建 | **24s**（25/24/24，中位 24） | 只有 `COPY src/` 之后的层失效 |
| **差值** | **≈7s** | 这才是「改代码的代价」 |

⚠️ 第一次构建（Docker 刚启动、缓存与磁盘都凉）会慢得多——实测首次数到 50s。
**别拿第一次的数当基线**：我在做上面这张表时先踩了这个坑，第一次测出「零改动 48s」而
「改代码 26s」这种自相矛盾的数，加了两次预热后才是上表结果。
这和「以性能为名做选择时，先把性能量出来」是同一条纪律的延伸：**量的时候先排除冷启动。**

镜像体积 **235MB**（Python 3.12/3.13 + fastapi + uvicorn[standard] 全量依赖）。
想更小可以去 `uvicorn[standard]` 里的 uvloop / httptools / watchfiles（后者容器里用不到）。

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `CLIENT_API_KEY` | ✅ | — | **对外凭据**。调用方发在 `X-API-Key` 头里的值，服务端只认它 |
| `LLM_API_KEY` | ✅ | — | **上游凭据**。服务端调 LLM 用，绝不外发。与上一项相同会拒绝启动 |
| `LLM_BASE_URL` | | `https://api.deepseek.com/v1` | LLM 服务地址 |
| `LLM_TIMEOUT` | | `30.0` | 单次请求超时（秒） |
| `MAX_CONCURRENT` | | `5` | 并发上限，由信号量控制 |
| `MAX_RETRIES` | | `3` | 可重试异常的最大重试次数 |
| `STREAM_DELAY_MS` | | `20` | 流式 token 间隔（毫秒）。W3 接真实模型后失效，测试里置 0 让用例秒过 |
| `DEMO_ROUTES_ENABLED` | | `true` | 是否挂载 `/demo/errors/{kind}` 调试端点。**生产应置 false**（关闭后返回 404，像不存在一样） |

必填项缺失时应用**启动即报错**（fail fast），不会拖到第一次调用。
取值优先级：**命令行环境变量 > `.env` 文件 > 类里的默认值**。字段名与变量名大小写不敏感（`llm_timeout` ↔ `LLM_TIMEOUT`）。

## 已实现的设计要点

- **配置**：`env_file` 用 `Path(__file__)` 锚定到项目根，从任何目录启动都能读到 `.env`（相对路径会按运行时 cwd 解析，是常见坑）
- **异常分类**：每个异常类带 `retryable` 类属性，重试逻辑只需 `if is_retryable(exc)`，新增异常类型不用改重试代码
- **日志**：用 `json.dumps` 而非格式串拼 JSON —— 消息里含引号或换行时，格式串方案会产出非法 JSON，采集端会直接丢掉该条日志
- **并发**：`gather` 并发（总耗时 ≈ 最慢的任务）对比 `for ... await` 串行（总耗时 = 各任务之和）；`wait_for` 超时会取消内部协程，且超时预算包含信号量排队时间
- **测试隔离**：用 `monkeypatch` 控环境变量 + `Settings(_env_file=None)` 关掉文件读取，测试不依赖开发者本机的 `.env`
- **密钥不落日志**：调试输出走 `mask_secret()` 脱敏（`sk-1***cdef`），完整密钥绝不进 stdout
- **鉴权**：`X-API-Key` 的校验写在依赖里（`verify_api_key`），受保护的端点只需在参数上写 `api_key: ApiKeyDep`。依赖**先于请求体校验执行**且失败即短路——实测「缺密钥 + 非法 body」返回 401 而不是 422，未授权调用方连"你字段名拼错了"都拿不到
- **两把钥匙分离**：对外凭据 `CLIENT_API_KEY` 与上游凭据 `LLM_API_KEY` 分开，且加校验禁止相同。早期实现用上游密钥直接做门禁卡，等于让客户端凭据一泄露就绕过服务刷上游账单
- **鉴权声明的两种写法**：手写 `Header(alias=...)` 会让 `X-API-Key` 以 `required=False` 的普通参数出现在 OpenAPI 里（Swagger UI 显示为选填、无锁，与实际 401 行为不符）；换成 `APIKeyHeader` 后生成 `security` 段，Swagger UI 出现 Authorize 按钮和锁图标。两者行为完全相同，**差别只在文档正确性上**——而文档错了比没有文档更坑
- **恒定时间比较**：密钥比对用 `secrets.compare_digest` 而非 `==`，避免通过响应耗时逐位猜出密钥
- **WS 的拒绝时机**：必须在 `accept()` **之前** `close()`，这样整个握手被拒（客户端看到 HTTP 403）；若先 accept 再 close，客户端会先认为"连上了"再收到关闭——"连上"和"被踢"变成两件事，客户端很容易写成 bug。实测两种写法在 TestClient 里的表现也不一样
- **可变全局状态包成依赖**：`ConnectionManager` 是模块级单例（WS 连接是**有状态**的，服务端必须记住谁在线），但它被包成 `get_manager()` 依赖——于是每个测试用例能换上全新实例，不会互相污染。这是 Day 3 那套依赖注入的第二次收益
- **WS 的错误协议要自己发明**：WS 里没有 422（状态码是 HTTP 概念）。本项目刻意让错误帧与 REST 的 422 响应体**同构**（同样是 `loc`/`type`/`msg`），客户端才能用一套解析逻辑处理两种协议；并且一次坏输入不会断开连接，可以继续对话
- **WS 的流式不需要发明协议**：直接一条条 `send_json` 即可，没有 `data:` 前缀、没有空行分隔符、没有多行前缀坑。代价是重连、序号、心跳全得自己写——这是 SSE 与 WS 权衡里最直观的一条
- **统一错误外壳**：四类异常来源（业务异常 / 请求体校验 / HTTP 层 / 未处理）经四个处理器产出**同构**的 JSON；`_json_error` 是唯一出口，想改结构只改一处
- **意外异常绝不回显**：`unhandled_error_handler` 的对外文案是固定常量，异常内容只进日志（用 `request_id` 捞回来）。真实事故就是这么泄露的——某个上游 SDK 把请求体拼进异常消息，而异常被透传给了客户端。`/demo/errors/unhandled` 故意在异常里塞了假密钥和假路径，并由 `probe_error_contract.py` 断言响应里一个字都不出现
- **响应头也要一起统一**：写"统一响应"最容易丢的就是头。401 的 `WWW-Authenticate`（RFC 9110 要求）与每个响应的 `X-Request-ID` 都得显式带上，后者还要在中间件侧 `setdefault` 去重——因为未处理异常的 500 由最外层框架发出，不经过我们的中间件
- **错误码是机器可读的稳定标识**：`error` 字段（如 `upstream_rate_limited`）与 `detail` 文案分离。文案可以随便改、可以翻译，`error` 码改了就是破坏性变更——客户端应该 `switch (error)`，而不是去匹配人话
- **每个请求一行结构化日志**：字段是独立的 JSON 键（`request_id`/`method`/`path`/`http_status`/`duration_ms`），能被日志系统直接分组与求分位；级别按状态码分（5xx→ERROR，其余→INFO），这个区分决定告警有没有用
- **日志在 `lifespan` 里配置，不在模块顶层**：避免 import 副作用，且时机晚于 uvicorn 的 `dictConfig`。这条是实测发现的——原先应用从不调用 `setup_logging()`，测试全绿而真实服务里访问日志被 `logging.lastResort` 整条丢弃
- **两条流分开**：我们的 JSON 走 stdout，uvicorn 自己的纯文本走 stderr。配合 `--no-access-log` 关掉框架内置访问日志，采集端只需面对一种格式

## 已知限制

1. **尚未接入真实 LLM**：目前的并发演示用的是模拟延迟（`async_demo.py`），真实调用在后续周次接入。
2. **`asyncio.Semaphore` 只在单进程内有效**：多 worker（`uvicorn --workers N`）或 K8s 多副本下，实际并发 = 进程数 × 信号量值，限流失效；分布式限流需要 Redis 令牌桶。
3. **覆盖率有排除项**：`async_demo.py`（可执行实验脚本）与各模块 `if __name__ == "__main__":` 下的本地演示代码不计入覆盖率，已在 `pyproject.toml` 中显式声明。库逻辑本身无排除。
4. **配置对象被 `lru_cache` 缓存**：进程运行期间修改环境变量不会生效；测试里需 `get_settings.cache_clear()`。
5. **容器内用 `PYTHONPATH=/app/src` 而非 editable 安装**：editable 安装会在源码变化时改写 `.venv` 内的 `.pth` / `dist-info`，导致 20MB 的依赖层被连带失效、重构建变慢。代价是镜像里没有 `ai-lab` 控制台脚本，只能用 `python -m ai_lab`。
6. **Windows + WSL2 下构建有约 17 秒固定开销**（零改动全缓存构建的中位耗时；改一行源码约 24 秒，**差值约 7 秒**）。开发期请用 `docker compose` 挂载源码，不要靠反复 `docker build` 迭代——挂载后改代码的重建时间是 0 秒。⚠️ 首次构建（Docker 刚起）会慢到 50s 量级，做基线对比前必须先预热，否则会得出自相矛盾的数（详见「Docker 运行 → 构建耗时」）。
7. **默认 PyPI 源指向清华镜像**（`[[tool.uv.index]]`，为国内网络环境配置）。海外网络下可删除该项以走官方源。
8. **WS 连接管理器是单进程内存态**：`ConnectionManager` 的集合只存在于当前进程。多 worker（`uvicorn --workers N`）或 K8s 多副本下，广播只能送达**本进程**的连接——跨进程广播需要 Redis pub/sub 之类的消息中间件。和已知限制 2（信号量限流）是同一类问题：**进程内状态在多实例部署下失效**。
9. **WS 没有重连、续传和业务序号协议**：客户端断开后要自己重连、自己去重、自己决定"从哪继续"。服务端发的 `index` 只是本连接的帧序号，不是可续传的消息 ID。要做断点续传得自己设计消息 ID + 服务端缓存。
10. **凭据放查询参数会进访问日志**：浏览器场景的无奈之举（`new WebSocket()` 设不了请求头）。生产环境需要在反向代理与日志采集层面做 URL 脱敏，或改用"连上后先发鉴权消息"的方案。
11. **目前的流式是"传输层真、生成层假"**：`build_reply` 返回 `str`，因此在发出第一个 token 之前整段回复已经生成完毕；`tokenize` 里的 `sleep` 只是把成品慢慢放出来，并不能让上游提前产出。同一个模型成本下实测（`scripts/probe_real_vs_fake_streaming.py`）：**假流式首字延迟 2.11s vs 真流式 0.30s（7 倍）**，总时长还多花 1.79s——生成时间被付了两遍（一遍真等模型，一遍假等节奏）。占位模型瞬间返回，所以本地看不出来。**W3 接真实模型时必须把流式入口改成 `AsyncIterator[str]`，否则首字延迟原封不动地翻上去。**
12. **`asyncio.sleep` 在 Windows 上会向上取整到约 15.6ms 的整数倍**（`scripts/probe_sleep_granularity.py` 实测并反推：三次运行得本机粒度 G ≈ 15.39 / 15.59 / 15.54ms，全域最大偏差 0.38~0.58ms，紧贴 Windows 真实刻度 15.625ms）。所以本机 `STREAM_DELAY_MS=20` 的实际节奏约 31ms/token（`ceil(20/15.6)=2`）——**「配置 20ms」和「实测 31ms」两个数都对**。对照实验：同机 `time.sleep` 精度在 1% 以内，所以这是**事件循环定时器**的问题，不是操作系统做不了精细睡眠。**同一个脚本在 CI 的 Linux runner 上跑出 1→1.1ms、20→20.1ms、100→100.2ms，一个刻度都没有**——「Linux 无此现象」是同脚本两平台的实测对照，不是推测。属「本地看着坏、线上是好的」。影响范围不止流式：将来用 `asyncio.sleep` 做重试退避 / 心跳周期 / 限流窗口时，每次都会多睡最多一个刻度（~15.6ms），在短周期场景下累积成可见偏差。该脚本已进 CI——但**只断言跨平台恒成立的不变量（不早返回），量化现象只报告不断言**，否则 Linux runner 上会无故变红。
13. **必须用 `--no-access-log` 起服务**：uvicorn 内置访问日志也写 stdout 且是纯文本，不关掉的话 stdout 会被两种格式混在一起（实测一半的行无法解析），`check_json_logs.py` 会（正确地）失败。**容器路径已强制**——该参数写死在 `Dockerfile` 的 `CMD` 里（同时写上 `--host 0.0.0.0`）。但**裸起 uvicorn 时仍靠文档 + CI 命令约束，应用内部无法强制**：这是 uvicorn 的启动参数，不是应用能控制的配置，除非包一层自己的启动脚本。目前接受这个缺口，因为它只在「不按文档起服务」时才咬人。
14. **一次未处理异常会产生 3 份 traceback**：中间件 1 份、异常处理器 1 份、uvicorn 兜底 1 份（框架这层关不掉）。前两份信息侧重不同（请求视角 vs 语义视角），故保留；代价是错误日志量约为正常情况的 3 倍。
15. **`/demo/errors/{kind}` 是调试端点**，用于验证错误契约；生产应把 `DEMO_ROUTES_ENABLED` 设为 false（关闭后这些路径返回 404，表现得像不存在）。
16. **`request_id` 是 12 位十六进制而非完整 UUID**：够用（碰撞概率可忽略）、且能安全放进 HTTP 头（头是 latin-1 编码，非 ASCII 值会直接抛 `UnicodeEncodeError`——写测试时把中文塞进 header 就会踩到）。同时它只用于**关联日志**，不承载任何业务含义。
