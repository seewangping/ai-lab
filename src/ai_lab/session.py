from dataclasses import dataclass, field
from typing import Protocol

class MessageLike(Protocol):
    role: str
    content: str


@dataclass
class Message:
    role: str
    content: str

@dataclass
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    max_turns: int = 20  # 保留的对话轮数（1 轮 = user + assistant），最多保留 max_turns * 2 条

    def add(self, role: str, content: str) -> None:
        """追加一条消息；超过 max_turns 轮时丢弃最早的轮次，保证首条为 user。"""
        self.messages.append(Message(role, content))
        self._trim()


    def _trim(self) -> None:
        """裁剪历史：最多保留 max_turns 轮，且保证首条为 user。"""
        limit = self.max_turns * 2

        # ① 先按条数裁：只留最后 limit 条
        if len(self.messages) > limit:
            del self.messages[: len(self.messages) - limit]

        while len(self.messages) > 1 and self.messages[0].role != "user":
            del self.messages[0]
        

    def history(self) -> list[dict[str, str]]:
        """转换成模型 API 需要的格式。"""
        return [{"role": m.role, "content": m.content} for m in self.messages]
        

    def last_user_input(self) -> str | None:
        for r in reversed(self.messages):
            if r.role == "user":
                return r.content

        return None
        
