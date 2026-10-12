"""结构化日志：让日志能被机器解析，而不只是给人看。

排障、成本核算、告警规则，全靠日志里带着时间 / 级别 / 模块名。
"""

import json
import logging
import sys
from datetime import datetime, timezone

# LogRecord 自带的属性集合。凡是【不在这里面】的键，就是调用方通过
# logging 的 extra= 传进来的业务字段，我们原样带进 JSON。
#
# 为什么值得这么干：把 method/path/status/duration_ms 塞进 msg 字符串里，
# 日志就从"可查询的数据"退化成了"只能用眼睛看的一行字"。
# 写成独立字段之后，日志系统可以直接按 path 分组统计、按 duration_ms 画分位线——
# 这才是"结构化日志"四个字真正的意思。
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """把每条日志渲染成一行 JSON。

    为什么不用 basicConfig(format='{"level":"%(levelname)s",...}') 那种「格式串拼 JSON」？
    因为 %(message)s 会被原样塞进 JSON，消息里一旦出现引号或换行，整行 JSON 就废了
    （日志采集器解析失败 = 这条日志等于没打）。用 json.dumps 转义才安全。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # extra= 传进来的字段原样并入。它们一定是 JSON 可序列化的简单值
        # （调用方负责），所以这里不做转换。
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS:
                payload[key] = value
        if record.exc_info is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志器。应用启动时调用一次即可。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # 先清空已有 handler：重复调用 setup_logging 会导致日志一行变多行
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """业务代码统一用这个拿 logger，而不是自己 logging.getLogger。"""
    return logging.getLogger(name)


if __name__ == "__main__":
    setup_logging()
    log = get_logger(__name__)
    log.info("服务启动完成")
    log.warning('这条消息里带"引号"和\\反斜杠，看 JSON 是否还能解析')
    try:
        1 / 0
    except ZeroDivisionError:
        log.exception("捕获到一个异常，异常栈会一起进 JSON")
