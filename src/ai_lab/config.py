"""应用配置：所有可变参数集中在这里，从环境变量 / .env 读取。

对照 C#：相当于 appsettings.json + IOptions<Settings>，
只是不需要 builder.Configuration 那套注册，一个类就够了。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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

    # 没有默认值 = 必填。四处都找不到就抛 ValidationError，应用拒绝启动
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
    print(f"llm_api_key    = {mask_secret(s.llm_api_key)}")
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
