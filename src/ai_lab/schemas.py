"""请求 / 响应模型：边界层的契约。

对照 C#：
  Pydantic 的 BaseModel ≈ 一个自带 DataAnnotations + FluentValidation 的 DTO，
  但校验规则写在【类型标注】和 Field() 里，而且失败时抛出的是【结构化数据】
  而不是一句话——FastAPI 直接把它序列化成 422 JSON。

一条核心认知（Day 2 验收问题）：
  校验放在边界层，不是"多此一举"，而是把"数据是否合法"和"业务怎么做"彻底分开。
  进了路由函数体的 message，已经是 str 且非空且长度合规——业务代码里不需要
  再写 if len(msg) == 0 之类的防御。散在各处的 if-else 校验，最终必然漏掉一处。
"""

from pydantic import BaseModel, ConfigDict, Field

# 会话 ID 的格式约束：同一份规则既用于请求校验，也用于路径参数（见 api.py）
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# 单条消息长度上限：防超大 payload 打爆内存/费用（生产里通常还会配合网关限流）
MAX_MESSAGE_LEN = 4000


class ChatRequest(BaseModel):
    """POST /chat 的请求体。字段名即 JSON 的 key。"""

    model_config = ConfigDict(
        # 多传了未声明的字段 → 直接 422（默认是静默忽略）。
        # 契约严格一点：前端把 sessionId 拼成 session_id 之外的名字时，立刻暴露。
        extra="forbid",
        # 自动去掉首尾空白。注意它发生在长度校验【之前】：
        # 所以 message="   " 会被 trim 成 ""，然后因 min_length=1 被拒——
        # 这正是我们想要的（否则全空格能绕过非空校验）。
        str_strip_whitespace=True,
    )

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LEN,
        description="用户这一轮的输入，去除首尾空白后不能为空",
        examples=["糖尿病酮症酸中毒的补液原则是什么？"],
    )
    session_id: str = Field(
        pattern=SESSION_ID_PATTERN,
        description="会话 ID，仅允许字母/数字/下划线/连字符，最长 64",
        examples=["sess-20260929-001"],
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="采样温度，0 最确定、2 最随机",
    )
    stream: bool = Field(default=False, description="是否流式返回（Day 4 接入）")


class ChatResponse(BaseModel):
    """POST /chat 的响应体。

    response_model 的两个作用（都应亲手验证一次）：
      1. **过滤**：路由返回的字典里若有多余键（内部 trace、原始 prompt 等），
         出站时会被裁掉——不会因为"顺手 return 了整个内部对象"而泄露。
      2. **文档**：/docs 的响应结构、以及 /openapi.json 的 schema 都由它生成。
    """

    reply: str
    session_id: str
    model: str = "stub-model"
    latency_ms: int = Field(ge=0, description="本次处理的耗时（毫秒）")


class ErrorResponse(BaseModel):
    """统一错误结构：Day 6 的全局异常处理器会用同样的外壳，客户端只需一套解析逻辑。"""

    error: str
    detail: str
    retryable: bool = False
