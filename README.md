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

当前状态：`mypy` 19 个文件零报错；`pytest` 86 个用例全过，覆盖率 **100%**。

端到端另有两层手动验证（需要服务在跑）：

```bash
uv run uvicorn ai_lab.api:app --port 8000     # 另开一个窗口
uv run python scripts/smoke_api.py            # 14 项冒烟：鉴权/校验/流式/响应头全查
uv run python scripts/probe_stream_timing.py  # 时序对照：TestClient 全是 0.000s，真实 HTTP 才有间隔
```

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

## 已知限制

1. **尚未接入真实 LLM**：目前的并发演示用的是模拟延迟（`async_demo.py`），真实调用在后续周次接入。
2. **`asyncio.Semaphore` 只在单进程内有效**：多 worker（`uvicorn --workers N`）或 K8s 多副本下，实际并发 = 进程数 × 信号量值，限流失效；分布式限流需要 Redis 令牌桶。
3. **覆盖率有排除项**：`async_demo.py`（可执行实验脚本）与各模块 `if __name__ == "__main__":` 下的本地演示代码不计入覆盖率，已在 `pyproject.toml` 中显式声明。库逻辑本身无排除。
4. **配置对象被 `lru_cache` 缓存**：进程运行期间修改环境变量不会生效；测试里需 `get_settings.cache_clear()`。
5. **容器内用 `PYTHONPATH=/app/src` 而非 editable 安装**：editable 安装会在源码变化时改写 `.venv` 内的 `.pth` / `dist-info`，导致 20MB 的依赖层被连带失效、重构建变慢。代价是镜像里没有 `ai-lab` 控制台脚本，只能用 `python -m ai_lab`。
6. **Windows + WSL2 下构建有约 23 秒固定开销**（零改动全缓存构建的耗时）。开发期请用 `docker compose` 挂载源码，不要靠反复 `docker build` 迭代。
7. **默认 PyPI 源指向清华镜像**（`[[tool.uv.index]]`，为国内网络环境配置）。海外网络下可删除该项以走官方源。
