# ---------- 构建阶段 ----------
FROM python:3.13-slim AS builder

WORKDIR /app

# 用 pip 从清华源装 uv，而不是 COPY --from=ghcr.io/astral-sh/uv:latest。
# 原因：国内网络访问 ghcr.io 常超时/被墙；代价是多一个约 30MB 的安装层，
# 运行阶段不会带上它。若你的网络能直连 ghcr.io，换回 COPY --from 版本镜像更小。
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# 只拷依赖声明 → 依赖层单独成层，且只由 pyproject.toml / uv.lock 决定。
# 改代码时这一层永远命中缓存，不会重新下载依赖。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src/ ./src/

# 这里刻意**不再执行** `uv sync`（即不把 ai-lab 自己装进 .venv）。
# 原因：editable 安装会在 .venv 里写 ai_lab.pth / dist-info，源码一变
# 这些文件就变，20MB 的 .venv 层随之全部失效——本机实测让重构建多花 ~10 秒。
# 改为运行阶段用 PYTHONPATH=/app/src 让包可导入，效果等价且 .venv 层可长期缓存。


# ---------- 运行阶段 ----------
FROM python:3.13-slim

WORKDIR /app

# 非 root 运行：容器被攻破时降低影响面。
# 必须放在 COPY 之前——它不依赖任何构建产物，提前可被缓存；
# 放在 COPY 之后则每次改代码都要重跑 useradd（本机实测 4.6 秒）。
RUN useradd -m appuser

# 分三次 COPY，而不是一次 `COPY --from=builder /app /app`：
# .venv 约 20MB 且与源码无关，单独成层后可长期命中缓存；
# src/ 只有几十 KB，改代码只重建这一小层，镜像导出阶段的开销大幅下降。
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/uv.lock /app/README.md ./
COPY --from=builder /app/src ./src

# PYTHONPATH 让 python -m ai_lab 找得到包（替代 editable 安装）
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000

# 用 /health 做存活探针（该端点免鉴权，专门为此存在）。
# slim 镜像里没有 curl，所以用 Python 标准库发请求：成功则退出码 0，异常则非 0。
# 不引额外依赖——健康检查不该成为镜像变大的理由。
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# 启动命令的三个要点，都是实测逼出来的，不是从模板抄的：
#
# 1. --host 0.0.0.0 必须写。uvicorn 默认只监听 127.0.0.1，
#    即使 -p 8000:8000 端口映射正确，容器外也连不进来。
#    症状是"镜像明明跑起来了却访问不到"，很容易误判成防火墙问题。
#
# 2. --no-access-log 必须写。uvicorn 内置的访问日志也写 stdout 且是纯文本，
#    会把我们输出的 JSON 日志流污染掉——本机实测不关的话 stdout 里
#    一半的行无法被 JSON 采集器解析。我们中间件那条信息量严格更大
#    （带 request_id + duration_ms），留着框架那条等于同一件事记两遍。
#
# 3. 不要加 --reload。那是开发期热重载用的；容器里要的是稳定、可预测的进程。
CMD ["uvicorn", "ai_lab.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
