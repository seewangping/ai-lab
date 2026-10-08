"""SSE 帧协议解剖 —— 把一帧拆到字符级，并演示那个"静默丢数据"的坑。

为什么单独写这个脚本：
  SSE 协议本体只有一页纸，但有两个点是"不亲手看过就一定会踩"的：
    ① 帧与帧之间靠【一个空行】分隔 —— 少一个空行，两个帧会被客户端当成一帧
    ② data 里含换行时，【每一行都要加 `data: ` 前缀】——
       只加第一行的话，后续行会被客户端当作"非法字段行"直接丢弃，
       服务端 grep 日志一切正常、客户端少了一半内容，这是最典型的静默数据丢失

  这个脚本用一个"照着规范写的最小客户端解析器"来对照：
  同样的原始字节，正确编码能拼回完整文本，错误编码会少一行。

用法：
  cd /d/ai-lab
  PYTHONPATH=src ./.venv/Scripts/python.exe scripts/demo_sse_protocol.py
"""

import json
import sys

from ai_lab.streaming import sse_comment, sse_frame, sse_json_frame

LINE = "=" * 74


def head(n: int, title: str) -> None:
    print(f"\n{LINE}\n[{n}] {title}\n{LINE}")


def show_raw(label: str, text: str) -> None:
    """逐行打印，每行前面加 | 和 · 标记，让"空行"和"行尾"都看得见。"""
    print(f"  {label}")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 编码结果以 \n 收尾，去掉 split 出来的这个多余空元素
    for line in lines:
        shown = line if line else "·(空行：这一行是帧的分隔符)"
        print(f"    | {shown}")
    print()


# ---------------------------------------------------------------- 最小客户端
def parse_sse(text: str) -> list[dict[str, object]]:
    """照 SSE 规范实现的最小客户端解析器（约 15 行）。

    规则摘自规范：
      · 帧与帧之间用【一个空行】分隔
      · `字段: 值` —— 冒号后的一个前导空格要去掉
      · 以 `:` 开头的行是注释（心跳），忽略
      · 多行 data 用 "\\n" 拼回
      · 没有 data 字段的帧不派发事件
    """
    events: list[dict[str, object]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event = value
        if not data_lines:
            continue  # 规范：没有 data 字段的帧不派发事件（心跳帧走的就是这条路）
        events.append({"event": event, "data": "\n".join(data_lines)})
    return events


def main() -> int:
    # ---------------------------------------------------------------- 1
    head(1, "一帧长什么样（注意最后那个空行）")
    frame = sse_frame("你好", event="token", event_id="7", retry_ms=3000)
    show_raw("sse_frame('你好', event='token', event_id='7', retry_ms=3000) 的原始字符：", frame)
    print("  逐行读：")
    print("    event: token   ← 事件名，浏览器 addEventListener('token', ...) 监听它")
    print("    id: 7          ← 事件 ID，断线重连时浏览器会带 Last-Event-ID 回来")
    print("    retry: 3000    ← 断线后等 3000ms 再重连")
    print("    data: 你好      ← 数据体")
    print("    (空行)         ← ★ 帧的结束标记。没有它，客户端认为帧还没收完")

    # ---------------------------------------------------------------- 2
    head(2, "★ 核心坑：data 里含换行时，每一行都要加 `data: ` 前缀")
    text = "第一行\n第二行"

    good = sse_frame(text, event="token")
    show_raw("【正确】sse_frame('第一行\\n第二行') —— 每行都加了前缀：", good)

    bad = "event: token\ndata: 第一行\n第二行\n\n"
    show_raw("【错误】手写成 data: 第一行 / 第二行（第二行漏了前缀）：", bad)

    print("  现在用同一个客户端解析器分别读一遍：")
    g = parse_sse(good)[0]["data"]
    b = parse_sse(bad)[0]["data"]
    print(f"    正确编码 → 客户端收到 {g!r}")
    print(f"    错误编码 → 客户端收到 {b!r}")
    print()
    print(f"    丢了什么：{text!r} 里的 '第二行' 不见了")
    print("    为什么：'第二行' 这一行没有 `字段:` 形式，客户端按规范当成非法行【直接忽略】")
    print("    危害：服务端日志正常、HTTP 200、帧数正常 —— 只有用户少看到内容")

    # ---------------------------------------------------------------- 3
    head(3, "所以结构化数据统一走 JSON：换行会被转义，天然保住'一帧一行'")
    payload = {"text": "第一行\n第二行", "index": 1}
    jf = sse_json_frame(payload, event="token")
    show_raw("sse_json_frame({'text': '第一行\\n第二行', 'index': 1})：", jf)
    print(f"  注意 data 里是字面的两个字符 \\n，不是真换行：{json.dumps(payload, ensure_ascii=False)!r}")
    got = json.loads(str(parse_sse(jf)[0]["data"]))
    print(f"  客户端 json.loads 之后换行又回来了：{got['text']!r}")
    print()
    print("  ensure_ascii=False 的作用（抓包/curl 时肉眼可读）：")
    print(f"    ensure_ascii=True  → {json.dumps(payload)}")
    print(f"    ensure_ascii=False → {json.dumps(payload, ensure_ascii=False)}")

    # ---------------------------------------------------------------- 4
    head(4, "心跳帧：以冒号开头，客户端必须忽略")
    hb = sse_comment("ka-20")
    show_raw("sse_comment('ka-20')：", hb)
    print(f"  客户端解析结果：{parse_sse(hb)}  ← 空列表，正确忽略")
    print("  存在的理由：长时间没数据的连接会被 Nginx / 负载均衡 / 云网关按空闲超时掐掉，")
    print("             心跳让中间层看到'连接还活着'。")

    # ---------------------------------------------------------------- 5
    head(5, "结束标记：协议里【没有】这个东西，是各家自己约定的")
    print("  SSE 规范定义的是「帧」，没定义「最后一帧长什么样」。")
    print("  服务端关掉响应体流 → 客户端触发 onerror 或 readyState=CLOSED。")
    print()
    print("    本项目约定     event: done   （见 streaming.py 最后一帧）")
    print("    OpenAI 约定    data: [DONE]")
    print()
    print("  两种都能用，关键是【客户端和服务端自己对齐】。")
    print("  用的是 fetch + ReadableStream 手解帧时，end-of-stream 要自己判。")

    # ---------------------------------------------------------------- 6
    head(6, "从正在运行的服务上抓一段真实字节")
    try:
        import httpx

        from ai_lab.config import get_settings

        settings = get_settings()
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=10.0) as http:
            with http.stream(
                "POST",
                "/chat/stream",
                json={"message": "短", "session_id": "demo-sse"},
                headers={"X-API-Key": settings.llm_api_key},
            ) as resp:
                print(f"  状态码 {resp.status_code}，content-type={resp.headers.get('content-type')}")
                print(f"  x-accel-buffering={resp.headers.get('x-accel-buffering')}  ← 少了它 Nginx 会攒缓冲")
                print()
                chunk = ""
                for line in resp.iter_lines():
                    chunk += line + "\n"
                    if chunk.count("\n\n") >= 3:
                        break
                show_raw("  前 3 帧的原始字符：", chunk)
    except Exception as exc:  # noqa: BLE001
        print(f"  服务未启动或请求失败（{type(exc).__name__}），跳过这一节。")
        print("  启动：PYTHONPATH=src ./.venv/Scripts/python.exe -m uvicorn ai_lab.api:app --port 8000")

    print(f"\n{LINE}\n结论：帧格式就 4 个字段 + 一个空行；坑全在'换行'和'缓冲区'上。\n{LINE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
