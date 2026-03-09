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
    session.history = None
    session.last_transcript = None
    
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

    sent = []
    async def fake_broadcast(payload):
        sent.append(payload)

    # 3) stub a fake llm_client that simulates a tool call
    class FakeChat:
        def __init__(self, tools):
            self.tools = tools # This is a list of FunctionTool objects
        
        async def __call__(self):
             # Simulate the LLM calling the function
            if self.tools:
                # The SDK pattern caches the instance on the tool call
                # In our mock, we just call the underlying function
                self.tools[0](
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
                await self.__call__()
            return gen()

    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda chat_ctx=None, tools=None, **kwargs: FakeChat(tools)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(
        pytest.test_agent, 
        pytest.test_session, 
        pytest.test_persona, 
        pytest.test_worker_id, 
        fake_broadcast
    )

    types = [p.get("type") for p in sent]
    assert "CONTRACT_INTENT" in types
    preview = next((p for p in sent if p.get("type") == "CONTRACT_PREVIEW"), None)
    assert preview is not None
    assert preview["contract_data"]["price"] == "$1.15/kg"

@pytest.mark.asyncio
async def test_extract_idempotency(monkeypatch):
    """Verify that multiple tool calls merge non-empty fields correctly (VIGOROUS)."""
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [
        SimpleNamespace(role="user", content="5 tons at $1.20")
    ])
    sent = []
    async def fake_broadcast(payload): sent.append(payload)

    class MultiCallChat:
        def __init__(self, tools): self.tools = tools
        def __aiter__(self):
            async def gen():
                # Call 1: Partial data
                self.tools[0](price="$1.20", product="White Maize")
                # Call 2: Add/Correction
                self.tools[0](quantity="5 tons", delivery="Nairobi")
                # Call 3: Correction of an existing field
                self.tools[0](price="$1.25")
                yield SimpleNamespace(delta=SimpleNamespace(content="done"))
            return gen()

    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda tools=None, **kw: MultiCallChat(tools)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Halima", "TEST", fake_broadcast)
    
    preview = next(p for p in sent if p["type"] == "CONTRACT_PREVIEW")
    data = preview["contract_data"]
    assert data["price"] == "$1.25" # Last write wins for that field
    assert data["quantity"] == "5 tons"
    assert data["product"] == "White Maize"
    assert data["delivery"] == "Nairobi"

@pytest.mark.asyncio
async def test_extract_data_type_coercion(monkeypatch):
    """Verify that tool inputs are coerced to strings (VIGOROUS)."""
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [SimpleNamespace(role="u", content="x")])
    sent = []
    async def fake_broadcast(payload): sent.append(payload)

    class TypeCoercionChat:
        def __init__(self, tools): self.tools = tools
        def __aiter__(self):
            async def gen():
                # Call with non-string types
                self.tools[0](price=1.20, quantity=5000, buyer=None)
                yield SimpleNamespace(delta=SimpleNamespace(content="ok"))
            return gen()

    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda tools=None, **kw: TypeCoercionChat(tools)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Halima", "T", fake_broadcast)
    
    preview = next(p for p in sent if p["type"] == "CONTRACT_PREVIEW")
    data = preview["contract_data"]
    assert data["price"] == "1.2"
    assert data["quantity"] == "5000"
    assert data["buyer"] == "Alex" # Alex is the default if "" is passed via str(None) then stripped

@pytest.mark.asyncio
async def test_extract_empty_history(monkeypatch):
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [])
    sent = []
    async def fake_broadcast(payload):
        sent.append(payload)

    class FakeEmptyChat:
        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(delta=None) 
            return gen()
    
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

@pytest.mark.asyncio
async def test_extract_unsupported_persona(monkeypatch):
    """Extraction should skip (no broadcasts) if persona is not Halima (Phase 17)."""
    sent = []
    async def b(p): sent.append(p)
    
    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Alex", "T", b)
    assert len(sent) == 0 # Should skip entirely

@pytest.mark.asyncio
async def test_extract_resolve_history_property(monkeypatch):
    """Verify that resolve_chat_ctx picks up session.history (Phase 17)."""
    sent = []
    async def b(p): sent.append(p)
    
    pytest.test_session.history = SimpleNamespace(items=[
        SimpleNamespace(role="user", content="Buying for $2")
    ])
    
    # Stub normalization to ensure our property was used
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: ctx.items if ctx else [])
    
    prompts = []
    class CaptureChat:
        def __init__(self, chat_ctx): 
            # items[1] is the User message containing the history
            prompts.append(str(chat_ctx.items[1].content))
        def __aiter__(self):
            async def gen(): yield SimpleNamespace(delta=None)
            return gen()

    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda chat_ctx, **kw: CaptureChat(chat_ctx)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Halima", "T", b)
    assert any("Buying for $2" in p for p in prompts)

@pytest.mark.asyncio
async def test_extract_fallback_transcript(monkeypatch):
    """Verify that extraction uses last_transcript if history is empty (Phase 17)."""
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [])
    pytest.test_session.last_transcript = "User said: I want it for free"
    
    sent = []
    async def b(p): sent.append(p)
    
    class CapturePromptChat:
        def __init__(self, chat_ctx):
            self.prompt = str(chat_ctx.items[1].content)
        def __aiter__(self):
            async def gen(): yield SimpleNamespace(delta=None)
            return gen()

    fake_llm = SimpleNamespace()
    # Capture the prompt sent to the LLM
    prompts = []
    fake_llm.chat = lambda chat_ctx, **kw: prompts.append(str(chat_ctx.items[1].content)) or CapturePromptChat(chat_ctx)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Halima", "T", b)
    assert any("I want it for free" in p for p in prompts)

@pytest.mark.asyncio
async def test_extract_retry_success(monkeypatch):
    """Verify that extraction retries once if first attempt yields no tool call (Phase 17)."""
    monkeypatch.setattr(agent_new, "normalize_chat_ctx", lambda ctx: [SimpleNamespace(role="u", content="x")])
    sent = []
    async def b(p): sent.append(p)
    
    call_count = 0
    class RetryChat:
        def __init__(self, tools):
            self.tools = tools
        async def __aiter__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # Succeed on second call
                self.tools[0](price="$50")
            yield SimpleNamespace(delta=None)

    fake_llm = SimpleNamespace()
    fake_llm.chat = lambda tools=None, **kw: RetryChat(tools)
    monkeypatch.setattr(agent_new, "llm_client", fake_llm)

    await agent_new.extract_and_preview(pytest.test_agent, pytest.test_session, "Halima", "T", b)
    
    assert call_count == 2
    preview = next(p for p in sent if p["type"] == "CONTRACT_PREVIEW")
    assert preview["contract_data"]["price"] == "$50"
