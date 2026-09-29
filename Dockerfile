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

CMD ["python", "-m", "ai_lab"]
