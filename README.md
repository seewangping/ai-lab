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
uv run python -m ai_lab   # 输出：Hello from ai-lab!
```

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

当前状态：`mypy` 21 个文件零报错；`pytest` 105 个用例全过，覆盖率 **100%**。

端到端另有两层手动验证（需要服务在跑，`uvicorn ai_lab.api:app --port 8000` 另开一个窗口）：

```bash
uv run python scripts/smoke_api.py            # HTTP：14 项冒烟（鉴权/校验/流式/响应头）
uv run python scripts/ws_client_demo.py       # WS：12 项真实客户端验证（握手拒绝/广播/双向）
uv run python scripts/probe_stream_timing.py  # 时序对照：TestClient 全是 0.000s，真实 HTTP 才有间隔
```

> **为什么这两层缺一不可**：TestClient 不经过真实网络栈——协议升级、帧编解码、
> 缓冲全被跳过。「被拒绝的握手，客户端到底看到什么」这类问题它答不了。
> 实测：TestClient 里握手被拒是 `WebSocketDisconnect`，真实客户端拿到的是
> **HTTP 403**（`InvalidStatus`）——同一个行为，两种表现。Day 4 的 SSE 时序也有同样的坑。

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

## 补充材料（写给 C# 出身的自己）

- `docs/python-syntax-for-csharp-devs.md` —— **读码五步法** + C#↔Python 语法对照表 + 本项目真实代码的逐行解码
- `scripts/syntax_tour.py` —— 13 节可运行的语法导览，每节都有 C# 对照和打印出来的证据

```bash
uv run python scripts/syntax_tour.py        # 跑全部
uv run python scripts/syntax_tour.py 4 8    # 只看推导式和装饰器
```

## Docker 运行

```bash
docker build -t ai-lab:dev .
docker run --rm --env-file .env ai-lab:dev
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

## 已知限制

1. **尚未接入真实 LLM**：目前的并发演示用的是模拟延迟（`async_demo.py`），真实调用在后续周次接入。
2. **`asyncio.Semaphore` 只在单进程内有效**：多 worker（`uvicorn --workers N`）或 K8s 多副本下，实际并发 = 进程数 × 信号量值，限流失效；分布式限流需要 Redis 令牌桶。
3. **覆盖率有排除项**：`async_demo.py`（可执行实验脚本）与各模块 `if __name__ == "__main__":` 下的本地演示代码不计入覆盖率，已在 `pyproject.toml` 中显式声明。库逻辑本身无排除。
4. **配置对象被 `lru_cache` 缓存**：进程运行期间修改环境变量不会生效；测试里需 `get_settings.cache_clear()`。
5. **容器内用 `PYTHONPATH=/app/src` 而非 editable 安装**：editable 安装会在源码变化时改写 `.venv` 内的 `.pth` / `dist-info`，导致 20MB 的依赖层被连带失效、重构建变慢。代价是镜像里没有 `ai-lab` 控制台脚本，只能用 `python -m ai_lab`。
6. **Windows + WSL2 下构建有约 23 秒固定开销**（零改动全缓存构建的耗时）。开发期请用 `docker compose` 挂载源码，不要靠反复 `docker build` 迭代。
7. **默认 PyPI 源指向清华镜像**（`[[tool.uv.index]]`，为国内网络环境配置）。海外网络下可删除该项以走官方源。
8. **WS 连接管理器是单进程内存态**：`ConnectionManager` 的集合只存在于当前进程。多 worker（`uvicorn --workers N`）或 K8s 多副本下，广播只能送达**本进程**的连接——跨进程广播需要 Redis pub/sub 之类的消息中间件。和已知限制 2（信号量限流）是同一类问题：**进程内状态在多实例部署下失效**。
9. **WS 没有重连、续传和业务序号协议**：客户端断开后要自己重连、自己去重、自己决定"从哪继续"。服务端发的 `index` 只是本连接的帧序号，不是可续传的消息 ID。要做断点续传得自己设计消息 ID + 服务端缓存。
10. **凭据放查询参数会进访问日志**：浏览器场景的无奈之举（`new WebSocket()` 设不了请求头）。生产环境需要在反向代理与日志采集层面做 URL 脱敏，或改用"连上后先发鉴权消息"的方案。
11. **目前的流式是"传输层真、生成层假"**：`build_reply` 返回 `str`，因此在发出第一个 token 之前整段回复已经生成完毕；`tokenize` 里的 `sleep` 只是把成品慢慢放出来，并不能让上游提前产出。同一个模型成本下实测（`scripts/probe_real_vs_fake_streaming.py`）：**假流式首字延迟 2.11s vs 真流式 0.30s（7 倍）**，总时长还多花 1.79s——生成时间被付了两遍（一遍真等模型，一遍假等节奏）。占位模型瞬间返回，所以本地看不出来。**W3 接真实模型时必须把流式入口改成 `AsyncIterator[str]`，否则首字延迟原封不动地翻上去。**
12. **`asyncio.sleep` 在 Windows 上会向上取整到 15.6ms 的整数倍**（实测 20ms → 31.7ms、50ms → 63.0ms）。所以本机 `STREAM_DELAY_MS=20` 的实际节奏约 31ms/token；Linux 事件循环无此问题，属"本地看着坏、线上是好的"。影响范围主要在未来若用它做重试退避 / 心跳周期 / 限流窗口时，每次会多睡最多 15.6ms，累积成偏差。
