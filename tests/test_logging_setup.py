"""日志模块测试：结构化日志的关键是「任何消息都不能把 JSON 打坏」。"""

import json
import logging
import sys
from collections.abc import Iterator

import pytest

from ai_lab.logging_setup import JsonFormatter, get_logger, setup_logging


@pytest.fixture
def restore_root_handlers() -> Iterator[None]:
    """setup_logging 会改根日志器，测完还原，别污染其他用例。"""
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    root.setLevel(saved_level)


def _record(msg: str, exc_info: bool = False) -> logging.LogRecord:
    if exc_info:
        try:
            raise ValueError("boom")
        except ValueError:
            return logging.LogRecord(
                name="ai_lab.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg=msg,
                args=(),
                exc_info=sys.exc_info(),
            )
    return logging.LogRecord(
        name="ai_lab.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_format_output_is_parseable_json() -> None:
    payload = json.loads(JsonFormatter().format(_record("普通消息")))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "ai_lab.test"
    assert payload["msg"] == "普通消息"
    assert "time" in payload


def test_special_characters_do_not_break_json() -> None:
    """消息里带引号、换行、反斜杠时，整行仍必须是合法 JSON。

    这正是「用格式串拼 JSON」做不到、必须用 json.dumps 的原因。
    """
    nasty = '带"引号"、\n换行 和 \\反斜杠'

    payload = json.loads(JsonFormatter().format(_record(nasty)))

    assert payload["msg"] == nasty


def test_exception_is_attached_to_payload() -> None:
    payload = json.loads(JsonFormatter().format(_record("出错", exc_info=True)))

    assert "exc" in payload
    assert "ValueError: boom" in str(payload["exc"])


def test_msg_args_are_rendered() -> None:
    """logging 的 %s 占位符要先渲染成最终消息，再进 JSON。"""
    record = logging.LogRecord(
        name="ai_lab.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="重试第 %d 次，等待 %.1f 秒",
        args=(3, 1.5),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "重试第 3 次，等待 1.5 秒"


def test_setup_logging_is_idempotent(restore_root_handlers: None) -> None:
    """重复初始化不应该让日志一行变多行。"""
    setup_logging()
    setup_logging()

    assert len(logging.getLogger().handlers) == 1


def test_get_logger_returns_named_logger() -> None:
    assert get_logger("ai_lab.foo").name == "ai_lab.foo"
