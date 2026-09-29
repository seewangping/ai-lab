from ai_lab.session import Session, Message
import pytest

def test_add_then_history_returns_messages_in_order() -> None:
    s = Session(session_id="t1")
    s.add("user", "你好")
    s.add("assistant", "你好，有什么可以帮你？")


    h = s.history()
    assert h == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]


def test_max_turns_trigger() -> None:
    s = Session(session_id="t1")
    s.add("user", "你好")
    s.add("assistant", "你好，有什么可以帮你？")
    s.max_turns = 2
    s.add("user", "你好2")
    s.add("assistant", "你好3")
    s.add("user", "你好3")

    h = s.history()
    assert h == [
        {"role": "user", "content": "你好2"},
        {"role": "assistant", "content": "你好3"},
        {"role": "user", "content": "你好3"},
    ]

def test_max_turns_just() -> None:
    s = Session(session_id="t1")
    s.add("user", "你好")
    s.add("assistant", "你好，有什么可以帮你？")
    s.max_turns = 4
    s.add("user", "你好2")
    s.add("user", "你好3")


    h = s.history()
    assert h == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
        {"role": "user", "content": "你好2"},
        {"role": "user", "content": "你好3"},
    ]


def test_empty_session() -> None:
    s = Session(session_id="t1")
    s.max_turns = 4
    
    l = s.last_user_input()
    assert l is None


def test_multiple_user() -> None:
    s = Session(session_id="t1")
    s.add("user", "你好")
    s.add("assistant", "你好，有什么可以帮你？")
    s.max_turns = 4
    s.add("user", "你好2")
    s.add("user", "你好3")


    l = s.last_user_input()
    assert l == "你好3"



@pytest.mark.parametrize(
    ("roles", "expected_roles"),
    [
        (["user"], ["user"]),
        (["user", "assistant"], ["user", "assistant"]),
        (["user", "assistant", "user"], ["user", "assistant", "user"]),
    ]
)

def test_history_preserves_role_order(roles: list[str], expected_roles: list[str]) -> None:
    s = Session(session_id="p")
    for i, role in enumerate(roles):
        s.add(role, f"msg{i}")
    assert [m["role"] for m in s.history()] == expected_roles

