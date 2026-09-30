"""占位 LLM：这是 W3 接真实模型时【唯一需要替换】的那一层。

为什么单独立一个模块，而不是把这段字符串拼在路由里：
  路由和流式生成器只依赖 `build_reply(payload, settings) -> str` 这个签名。
  W3 换成真实的 httpx 调用（带超时、重试、错误分类）时，签名不变，
  上层 api.py / streaming.py 一行都不用改——这就是"接缝"的价值。

  反例：如果回复是在路由函数里直接拼接的，那 W3 就得去动路由体，
  改鉴权、改校验的风险全跟着进来。**可变的部分要关在一个盒子里。**
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
