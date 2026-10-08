"""应用配置：所有可变参数集中在这里，从环境变量 / .env 读取。

对照 C#：相当于 appsettings.json + IOptions<Settings>，
只是不需要 builder.Configuration 那套注册，一个类就够了。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 把 .env 锚定到项目根（config.py 在 src/ai_lab/ 下，parents[2] 即 D:\ai-lab）。
# 不锚定的话 env_file 相对「运行时工作目录」解析——VS Code / 计划任务 /
# 别的目录启动时都会读不到。Day 6 进 Docker 前这步必须做，现在提前做了。
_BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """字段名即配置键，大小写不敏感（llm_timeout 对应 LLM_TIMEOUT）。"""

    # env_file: 从 .env 文件读候选值（绝对路径，与启动目录无关）
    # extra="ignore": .env 里多写了没用到的键不报错
    model_config = SettingsConfigDict(env_file=_BASE_DIR / ".env", extra="ignore")

    # ── 两把钥匙，职责不同，绝不能是同一把 ─────────────────────────────────
    # 没有默认值 = 必填。四处都找不到就抛 ValidationError，应用拒绝启动

    # 【对外凭据】调用方发在 X-API-Key 头里的值，服务端只认它。
    # 它可以按调用方分发、可以轮换、可以吊销——因为它只在你的边界内流通。
    client_api_key: str

    # 【上游凭据】调用第三方 LLM 用的 key。只留在这台机器的 .env 里，
    # 绝不能回显、绝不能进响应体、绝不能进日志。
    #
    # 为什么必须拆开：W2 早期对外校验直接比对的这把 key，
    # 后果是「谁拿到你服务的密钥，谁就同时拿到上游 LLM 的密钥」——
    # 他可以不经过你的服务，直接拿它去刷你的账单。
    # 两把钥匙的失效域也不同：客户端密钥泄露 → 吊销重发即可；
    # 上游密钥泄露 → 要动的是真金白银的账号。
    llm_api_key: str

    # 有默认值 = 可选，环境里没配就用这个
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_timeout: float = 30.0
    max_concurrent: int = 5
    max_retries: int = 3

    # 流式输出里每个 token 之间的间隔（毫秒）。W3 接真实 LLM 后这个值就没用了
    # （节奏由上游决定），现在用来模拟"打字机效果"。
    # 之所以做成配置项：测试里覆盖成 0 就能让流式用例瞬间跑完——这就是
    # "配置驱动的行为可以被测试"的实际用途，而不是为了好看。
    stream_delay_ms: float = Field(default=20.0, ge=0.0)

    @model_validator(mode="after")
    def _keys_must_differ(self) -> "Settings":
        """两把钥匙不能是同一个值——相同就等于没拆，拆了个寂寞。

        把这条写成校验而不是写在文档里：文档会被跳过，校验不会。
        配置写错的代价是启动失败（几秒钟就发现），而不写这条的代价是
        线上密钥泄露（很久才发现）。
        """
        if self.client_api_key == self.llm_api_key:
            raise ValueError(
                "CLIENT_API_KEY 不能与 LLM_API_KEY 相同："
                "否则对外密钥一泄露，上游密钥就一起泄露了。请改成两个不同的值。"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """全局唯一实例：首次调用读 .env 并校验，之后直接返回缓存，不重复读盘。"""
    return Settings()


def mask_secret(value: str, keep: int = 4) -> str:
    """把密钥脱敏成 sk-1***cdef 的形式，用于日志与调试输出。

    直接打印完整密钥是真实事故：日志会被采集、转发、存盘，等于把密钥公开。
    """
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}***{value[-keep:]}"


def call() -> None:
    """打印当前生效的全部配置（仅本地调试用，密钥脱敏）。"""
    s = get_settings()
    print(f"client_api_key = {mask_secret(s.client_api_key)}   （对外凭据，调用方发 X-API-Key）")
    print(f"llm_api_key    = {mask_secret(s.llm_api_key)}   （上游凭据，绝不外发）")
    print(f"llm_base_url   = {s.llm_base_url}")
    print(f"llm_timeout    = {s.llm_timeout}")
    print(f"max_concurrent = {s.max_concurrent}")
    print(f"max_retries    = {s.max_retries}")


# __main__ 守卫：直接运行本文件才打印，被 import 时静默。
# 不加的话，任何模块 import config 都会把 API Key 打到控制台。
# 注意：def call 必须写在这行之前——模块自上而下执行，
# 执行到这里时如果 call 还没定义，会直接 NameError（C# 方法顺序无关，Python 有所谓）。
if __name__ == "__main__":
    # 练习 3 观察点：注释/恢复 @lru_cache，对比这两次调用的输出差异
    s1 = get_settings()
    s2 = get_settings()
    print("两次拿到的是同一个对象吗:", s1 is s2)
    call()
