"""入口点测试：main() 是 [project.scripts] 暴露的命令行入口，坏了等于装完跑不起来。"""

import runpy

import pytest

from ai_lab import main
from ai_lab.config import call, get_settings, mask_secret


def test_module_entrypoint_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """模拟 `python -m ai_lab` —— 容器 CMD 走的就是这条路径，必须能跑通。"""
    runpy.run_module("ai_lab.__main__", run_name="__main__")

    assert "Hello from ai-lab!" in capsys.readouterr().out


def test_main_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    main()

    assert "Hello from ai-lab!" in capsys.readouterr().out


def test_call_prints_current_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """call() 打印的必须是「当前生效」的值（环境变量优先），且密钥必须脱敏。"""
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    monkeypatch.setenv("LLM_TIMEOUT", "99")

    call()

    out = capsys.readouterr().out
    assert "sk-test-key" not in out  # 完整密钥绝不能出现在输出里
    assert "sk-t***-key" in out  # 只显示脱敏形态
    assert "99.0" in out


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sk-1234567890abcdef", "sk-1***cdef"),
        ("short", "***"),  # 太短的串一律全掩，避免「脱敏」后还能猜出原文
    ],
)
def test_mask_secret(raw: str, expected: str) -> None:
    assert mask_secret(raw) == expected
