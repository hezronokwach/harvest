import asyncio
from types import SimpleNamespace

# Simple in-memory dispatcher
listeners = []

def register_listener(cb):
    listeners.append(cb)

async def broadcast_data(payload):
    print("BROADCAST:", payload)
    for cb in list(listeners):
        if asyncio.iscoroutinefunction(cb):
            asyncio.create_task(cb(payload))
        else:
            cb(payload)

# Minimal negotiation_agent and session
negotiation_agent = SimpleNamespace()
negotiation_agent.last_chat_ctx = [SimpleNamespace(role="user", content="I want 5 tons")]
negotiation_agent.pending_contract_data = {}
negotiation_agent.is_awaiting_approval = False

session = SimpleNamespace()
session.chat_ctx = None

persona = "Halima"

# Simple extraction simulation (no LLM)
async def extract_and_preview_sim():
    await broadcast_data({"type":"CONTRACT_INTENT","agent":persona,"status":"drafting"})
    history = negotiation_agent.last_chat_ctx or session.chat_ctx
    if not history:
        await broadcast_data({"type":"CONTRACT_PREVIEW_ERROR","agent":persona,"error":"empty_history"})
        negotiation_agent.is_awaiting_approval = False
        return
    preview = {"buyer":"Alex","product":"Maize","price":"$1.15/kg","quantity":"5000kg","delivery":"Mombasa","payment":"50/50"}
    negotiation_agent.pending_contract_data = preview
    negotiation_agent.is_awaiting_approval = True
    await broadcast_data({"type":"CONTRACT_PREVIEW","agent":persona,"preview": preview})

# Simulated Alex handler
def alex_on_data(payload):
    print("ALEX RECV:", payload)
    if payload.get("type") == "CONTRACT_PREVIEW":
        # auto-approve
        asyncio.create_task(broadcast_data({"type":"CONTRACT_APPROVED","from":"Alex"}))

# Halima handler
def halima_on_data(payload):
    print("HALIMA RECV:", payload)
    if payload.get("type") == "CONTRACT_APPROVED":
        negotiation_agent.is_awaiting_approval = False
        # broadcast file shared
        asyncio.create_task(broadcast_data({
            "type":"FILE_SHARED",
            "from": persona,
            "filename":"maize_supply_contract_final.pdf",
            "contract_data": negotiation_agent.pending_contract_data
        }))

async def run_sim():
    register_listener(alex_on_data)
    register_listener(halima_on_data)
    # simulate extraction
    await extract_and_preview_sim()
    # wait a moment for broadcasts to propagate
    await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(run_sim())
