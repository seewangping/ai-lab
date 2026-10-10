"""占位 LLM：这是 W3 接真实模型时【唯一需要替换】的那一层。

为什么单独立一个模块，而不是把这段字符串拼在路由里：
  路由和流式生成器都只依赖 `build_reply(payload, settings) -> str` 这个签名，
  换模型时改动只落在这个文件里。**可变的部分要关在一个盒子里。**

  反例：如果回复是在路由函数里直接拼接的，那 W3 就得去动路由体，
  改鉴权、改校验的风险全跟着进来。

⚠️ 但这里原先有一句判断是错的，2026-10-10 实测后纠正：

  原话写的是"W3 换成真实 httpx 调用时签名不变，上层 api.py / streaming.py
  一行都不用改"——**这句话不成立**。

  因为 `-> str` 意味着整段回复全部生成完了才返回，上层拿到的是一个成品。
  于是 streaming.py 和 ws.py 只能"先拿到全量、再慢慢放出去"，那是假流式：
  实测同一份模型成本下，这里的首字延迟（TTFT）2.11s，而真流式只要 0.30s，
  差 7 倍——多出来的时间正好是整段生成时间。

  证据可复跑：python scripts/probe_real_vs_fake_streaming.py

  所以 W3 要做的是**提供两个入口**，而不是替换一个：
      build_reply(...)  -> str                    非流式端点用（/chat）
      stream_reply(...) -> AsyncIterator[str]     流式端点用（/chat/stream、/ws/chat）

  后者会让 streaming.py 与 ws.py 各改一处调用点，tokenize() 则退化为
  仅供占位模型演示使用。**返回类型决定了能力边界**——这是这句话的实例。
"""

from ai_lab.config import Settings
from ai_lab.schemas import ChatRequest

# 出现在响应体里的模型名（W3 换成真实模型名，如 deepseek-chat）
MODEL_NAME = "stub-model"


def build_reply(payload: ChatRequest, settings: Settings | None = None) -> str:
    """假回复：把关键参数回显出来，方便一眼确认参数确实传到了这一层。

    中文注释里写"收到 N 字"是刻意的——流式输出下中文逐字显示更好观察。
    """
    extra = f"，max_concurrent={settings.max_concurrent}" if settings is not None else ""
    return (
        f"[stub] 收到 {len(payload.message)} 字的提问，会话 {payload.session_id}，"
        f"温度 {payload.temperature}，流式 {payload.stream}{extra}。"
        f"这是占位回复，W3 会把它换成真实的模型输出。"
    )
