import pytest
from types import SimpleNamespace

# Importing from agent_new
from agent_new import normalize_chat_ctx, resolve_chat_ctx

class DummyAgent:
    def __init__(self):
        self.last_chat_ctx = None
        self.chat_ctx = None

class DummySession:
    def __init__(self):
        self.chat_ctx = None

def test_normalize_none():
    assert normalize_chat_ctx(None) == []

def test_normalize_as_messages():
    msgs = [SimpleNamespace(role="user", content="hello")]
    class HasAs:
        def as_messages(self):
            return msgs
    assert normalize_chat_ctx(HasAs()) == msgs

def test_normalize_messages_attr():
    ctx = SimpleNamespace(messages=[{"role":"assistant","content":"ok"}])
    res = normalize_chat_ctx(ctx)
    assert len(res) == 1
    assert res[0].role == "assistant"
    assert res[0].content == "ok"

def test_normalize_raw_list_dicts():
    raw = [{"role":"user","content":"please"}]
    res = normalize_chat_ctx(raw)
    assert res[0].role == "user"
    assert res[0].content == "please"

def test_resolve_chat_ctx_priority():
    agent = DummyAgent()
    session = DummySession()
    agent.last_chat_ctx = "last"
    agent.chat_ctx = "agent"
    session.chat_ctx = "sess"
    assert resolve_chat_ctx(agent, session) == "last"
    agent.last_chat_ctx = None
    assert resolve_chat_ctx(agent, session) == "agent"
    agent.chat_ctx = None
    assert resolve_chat_ctx(agent, session) == "sess"
