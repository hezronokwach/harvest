import pytest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

# Importing from agent_new
import agent_new

@pytest.fixture(autouse=True)
def patch_globals(monkeypatch):
    """
    Prepare minimal global objects expected by extract_and_preview.
    """
    # dummy negotiation_agent & session
    agent = SimpleNamespace()
    agent.last_chat_ctx = None
    agent.pending_contract_data = {}
    agent.is_awaiting_approval = False

    session = SimpleNamespace()
    session.chat_ctx = None
    
    # Store for use in tests
    pytest.test_agent = agent
    pytest.test_session = session

    # persona and worker_id
    pytest.test_persona = "Halima"
    pytest.test_worker_id = "TEST-HALIMA"

@pytest.mark.asyncio
async def test_extract_success(monkeypatch):
    # 1) make normalize_chat_ctx return a small message history
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [
        SimpleNamespace(role="assistant", content="Welcome"),
        SimpleNamespace(role="user", content="I want 5 tons")
    ])

    # 2) capture broadcast_data calls
    sent = []
    async def fake_broadcast(payload):
        sent.append(payload)

    # 3) stub a fake llm_client that simulates a tool call
    class FakeChat:
        def __init__(self, fnc_ctx):
            self.fnc_ctx = fnc_ctx
        
        async def __call__(self):
             # Simulate the LLM calling the function
            if self.fnc_ctx:
                self.fnc_ctx.submit_terms(
                    buyer="Alex",
                    product="Maize",
                    price="$1.15/kg",
                    quantity="5000kg",
                    delivery="Mombasa",
                    payment="50/50"
                )
            return self

        def __await__(self):
            return self.__call__().__await__()

        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(delta=SimpleNamespace(content=""))
            return gen()

    fake_llm = SimpleNamespace()
    def fake_chat_method(chat_ctx=None, fnc_ctx=None, **kwargs):
        return FakeChat(fnc_ctx)
    
    fake_llm.chat = fake_chat_method
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    # 4) Run extract_and_preview with explicit args
    await agent_new.extract_and_preview(
        pytest.test_agent, 
        pytest.test_session, 
        pytest.test_persona, 
        pytest.test_worker_id, 
        fake_broadcast
    )

    # Assertions
    types = [p.get("type") for p in sent]
    assert "CONTRACT_INTENT" in types
    assert any(t == "CONTRACT_PREVIEW" for t in types)

@pytest.mark.asyncio
async def test_extract_empty_history(monkeypatch):
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [])
    sent = []
    async def fake_broadcast(payload):
        sent.append(payload)

    # Mock LLM to do nothing (no tool call)
    class FakeEmptyChat:
        def __await__(self):
            yield
            return self
    
    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda **kwargs: FakeEmptyChat()
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(
        pytest.test_agent, 
        pytest.test_session, 
        pytest.test_persona, 
        pytest.test_worker_id, 
        fake_broadcast
    )
    types = [p.get("type") for p in sent]
    assert "CONTRACT_PREVIEW_ERROR" in types
    assert pytest.test_agent.is_awaiting_approval is False
