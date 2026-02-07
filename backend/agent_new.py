from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    JobProcess,
    cli,
    room_io,
    StopResponse,
)
from livekit.agents.voice import (
    AgentStateChangedEvent,
    UserInputTranscribedEvent,
    ConversationItemAddedEvent,
)
from livekit.plugins import silero, noise_cancellation, deepgram, groq, hume, azure
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit import rtc
import logging
import asyncio
import os
import random
from pathlib import Path
import re

# -------------------------------------------------
# Env
# -------------------------------------------------
if not load_dotenv():
    load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if not os.getenv("LIVEKIT_URL") and os.getenv("NEXT_PUBLIC_LIVEKIT_URL"):
    os.environ["LIVEKIT_URL"] = os.getenv("NEXT_PUBLIC_LIVEKIT_URL")

# Configure logging: silence noisy internal streams, show only essential negotiation logs
logging.basicConfig(level=logging.WARNING) 
logger = logging.getLogger("negotiation-agent")
logger.setLevel(logging.INFO)

import json
from typing import Annotated
from pydantic import Field
from livekit.agents import llm
from types import SimpleNamespace

# --- Global Placeholders for Mocking (Tests Only) ---
# In production, these are scoped to the entrypoint.
llm_client = None 

# -------------------------------------------------
# Handshake Helpers (Top Level for Testing)
# -------------------------------------------------
def resolve_chat_ctx(agent_obj, session_obj, fallback=None):
    """
    Return a chat context object (or None). Handles:
     - negotiation_agent.last_chat_ctx (already a ChatContext)
     - agent_obj.chat_ctx (if agent object exposes it)
     - session_obj.chat_ctx (if present)
     - fallback value
    """
    if hasattr(agent_obj, "last_chat_ctx") and agent_obj.last_chat_ctx:
        return agent_obj.last_chat_ctx
    if hasattr(agent_obj, "chat_ctx") and getattr(agent_obj, "chat_ctx"):
        return agent_obj.chat_ctx
    if hasattr(session_obj, "chat_ctx") and getattr(session_obj, "chat_ctx"):
        return session_obj.chat_ctx
    return fallback

def normalize_chat_ctx(ctx):
    """
    Normalize different chat context shapes to a list of messages with .role and .content.
    Returns list of SimpleNamespace(role=<>, content=<>)
    """
    if ctx is None:
        return []
    
    # 1. object already supports as_messages()
    if hasattr(ctx, "as_messages") and callable(getattr(ctx, "as_messages")):
        return ctx.as_messages()

    # 2. ctx has .messages or .history attribute
    if hasattr(ctx, "messages"):
        raw = ctx.messages
    elif hasattr(ctx, "history"):
        raw = ctx.history
    else:
        raw = ctx

    # 3. Single string -> one message
    if isinstance(raw, str):
        return [SimpleNamespace(role="system", content=raw)]

    # 4. Normalize list/iterable
    msgs = []
    if isinstance(raw, (list, tuple)):
        for m in raw:
            if m is None: continue
            if hasattr(m, "role") and hasattr(m, "content"):
                msgs.append(SimpleNamespace(role=m.role, content=m.content))
            elif isinstance(m, dict):
                msgs.append(SimpleNamespace(role=m.get("role", "unknown"), content=m.get("content", "")))
            else:
                msgs.append(SimpleNamespace(role="unknown", content=str(m)))
    return msgs

def truncate_text(s, n=300):
    if s is None:
        return ""
    if isinstance(s, (list, tuple)):
        # join parts into a string first
        s = " ".join(str(getattr(m, "content", m)) for m in s)
    # ensure we slice the string value, not a slice object
    return str(s)[:n]

def normalize_content_for_llm(c, worker_id="AGENT"):
    # content for llm.ChatMessage must be list[str]
    if isinstance(c, (list, tuple)):
        return [str(x) for x in c]
    if isinstance(c, slice):
        # defensive: convert slice to empty string and log (shouldn't happen)
        logger.error("%s Unexpected slice object passed as content: %s", worker_id, c)
        return [""]
    return [str(c)]

async def consume_llm_stream(stream):
    """Aggregate chunks from an LLM stream into a single string"""
    text = ""
    async for chunk in stream:
        delta = getattr(chunk, "delta", None)
        if delta is None: continue
        content = getattr(delta, "content", None)
        if content is None: continue
        text += "".join(content) if isinstance(content, (list, tuple)) else str(content)
    return text

class ContractExtractionContext(llm.FunctionContext):
    """Context for LLM to submit structured agreement terms."""
    
    def __init__(self):
        super().__init__()
        self.extracted_data = None

    @llm.ai_callable(description="Submit final negotiated terms for the maize supply contract.")
    def submit_terms(
        self,
        buyer: Annotated[str, llm.TypeInfo(description="Name of the buyer (e.g. Alex)")],
        product: Annotated[str, llm.TypeInfo(description="Product being sold (e.g. White Maize)")],
        price: Annotated[str, llm.TypeInfo(description="Negotiated price (e.g. $1.20/kg)")],
        quantity: Annotated[str, llm.TypeInfo(description="Total quantity (e.g. 5 tons)")],
        delivery: Annotated[str, llm.TypeInfo(description="Delivery location or terms")],
        payment: Annotated[str, llm.TypeInfo(description="Payment details (e.g. Mobile Money, 50% upfront)")]
    ):
        self.extracted_data = {
            "buyer": buyer, "product": product, "price": price,
            "quantity": quantity, "delivery": delivery, "payment": payment
        }
        logger.info("✅ Tool Call Received: %s", self.extracted_data)

async def extract_and_preview(agent, session, persona, worker_id, broadcast_data):
    """Trigger LLM Term Extractor (Phase 8 Reliability)"""
    global llm_client
    
    print(f"DEBUG: 🚀 {worker_id} [TASK START] extract_and_preview")
    # 1. IMMEDIATE Feedback - Signal "Drafting" status BEFORE LLM call
    logger.warning(f"{worker_id} 📤 Broadcasting CONTRACT_INTENT (Instant Feedback)")
    await broadcast_data({
        "type": "CONTRACT_INTENT",
        "agent": persona,
        "status": "drafting"
    })

    try:
        # 2. Robust History Extraction
        ctx_to_use = resolve_chat_ctx(agent, session, fallback=None)
        logger.debug("%s resolved ctx_to_use type=%s repr=%r", worker_id, type(ctx_to_use), getattr(ctx_to_use, "__dict__", repr(ctx_to_use))[:300])
        
        try:
            history_messages = normalize_chat_ctx(ctx_to_use)
        except Exception as e:
            logger.error(f"{worker_id} Error normalizing chat context: {e}")
            history_messages = []

        # If history is empty, provide a hint to the LLM
        if not history_messages:
            history_text = "No prior messages available. Negotiation just started."
        else:
            # Ensure each contribution to history is flattened to a readable string
            history_parts = [str(getattr(m, "content", m)) for m in history_messages]
            raw_history = "\n".join([f"{m.role}: {history_parts[i]}" for i, m in enumerate(history_messages)])
            history_text = truncate_text(raw_history, 500)
        
        print(f"DEBUG: 📝 {worker_id} History Context Size: {len(history_messages)} items (Truncated to {len(history_text)})")

        # Construct messages for extraction (Defensive list-wrapping for Pydantic)
        messages = [
            {"role": "system", "content": "You are a specialized Term Extractor for Kenyan maize deals. Analyze the history and once you have the terms, call 'submit_terms'. If a term is unknown, use an empty string \"\" instead of null."},
            {"role": "user", "content": f"History:\n{history_text}\nExtract terms from the above."}
        ]
        
        print(f"DEBUG: 🧠 {worker_id} Extracting terms with prompt: {[(m['role'], m['content'][:100]) for m in messages]}")
        
        # Provision client if not exists
        if llm_client is None:
            llm_client = groq.LLM(model="llama-3.1-8b-instant")
        
        # Form final messages with strictly normalized content
        llm_messages = [
            llm.ChatMessage(role=m["role"], content=normalize_content_for_llm(m["content"], worker_id)) 
            for m in messages
        ]

        # Catch-all debug for the "slice" or "None" crash - safe string conversion
        logger.debug(
            "%s LLM payload verification: %s",
            worker_id,
            [(m.role, [str(x) for x in (m.content or [])][:2]) for m in llm_messages]
        )
        
        # Defensive assertion to fail early in logs if normalization fails
        for m in llm_messages:
            if not isinstance(m.content, list) or not all(isinstance(x, str) for x in m.content):
                raise ValueError(f"CRITICAL: LLM message content must be list[str], got {type(m.content)} with {m.content}")

        # Instantiate the tool context
        fnc_ctx = ContractExtractionContext()

        # Start the chat with the tool
        chat = llm_client.chat(chat_ctx=llm.ChatContext(items=llm_messages), fnc_ctx=fnc_ctx)
        
        # We await the full completion of the tool call/response
        await chat
        
        extracted_data = fnc_ctx.extracted_data
        if not extracted_data:
            logger.warning(f"{worker_id} LLM did not call the extraction tool.")
            extracted_data = {}

        # Sanity Check: Ensure we have at least SOME data
        meaningful_keys = ["buyer", "product", "price", "quantity", "delivery", "payment"]
        is_empty = not any(extracted_data.get(k) for k in meaningful_keys)
        
        if is_empty:
            logger.error(f"{worker_id} Term extraction yielded empty data. Aborting preview.")
            await broadcast_data({
                "type": "CONTRACT_PREVIEW_ERROR",
                "agent": persona,
                "error": "empty_extraction",
                "message": "I couldn't catch the deal details clearly. Please mention price and quantity again."
            })
            agent.is_awaiting_approval = False
            return

        # Populate missing fields with defaults
        defaults = {
            "buyer": "Alex", "product": "Maize", "price": "Negotiated",
            "quantity": "Negotiated", "delivery": "Discussed", "payment": "Discussed"
        }
        print(f"DEBUG: 📋 {worker_id} Extracted Data: {extracted_data}")
        agent.pending_contract_data = {**defaults, **extracted_data}
        
        preview_payload = {
            "type": "CONTRACT_PREVIEW",
            "contract_id": f"ctr_{random.getrandbits(16)}_{persona}",
            "agent": persona,
            "contract_data": agent.pending_contract_data,
            "title": "Maize Supply Agreement (Draft)"
        }
        print(f"DEBUG: 📤 {worker_id} Broadcasting preview payload keys: {list(preview_payload.keys())}")
        await broadcast_data(preview_payload)

    except Exception as e:
        logger.error(f"{worker_id} Critical Error in Term Extraction: {e}")
        err_text = str(e)
        await broadcast_data({
            "type": "CONTRACT_PREVIEW_ERROR",
            "agent": persona,
            "error": err_text[:500]
        })
        agent.is_awaiting_approval = False

# -------------------------------------------------
# Agent Class
# -------------------------------------------------
class NegotiationAgent(Agent):
    def __init__(self, instructions: str, persona: str, ctx: JobContext):
        super().__init__(instructions=instructions)
        self.persona = persona
        self.ctx = ctx
        self.is_awaiting_approval = False
        self.pending_contract_data = {}
        self.last_chat_ctx = None # Capture context for background tasks

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        """Idiomatic way to silence the agent during the drafting phase."""
        self.last_chat_ctx = turn_ctx # Robust history capture
        if self.is_awaiting_approval:
            print(f"DEBUG: 🤫 [SILENCE] {self.persona} is awaiting contract approval. Aborting response.")
            raise StopResponse()

# -------------------------------------------------
# Server Setup
# -------------------------------------------------
server = AgentServer()

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

server.setup_fnc = prewarm

# -------------------------------------------------
# Entrypoint
# -------------------------------------------------
@server.rtc_session()
async def entrypoint(ctx: JobContext):
    # Resolve role and persona from metadata
    agent_name = ctx.job.agent_name
    role = "seller"
    persona = "Halima"

    if ctx.job.metadata:
        try:
            meta = json.loads(ctx.job.metadata)
            role = meta.get("role", role)
            persona = meta.get("persona", persona)
        except Exception as e:
            logger.error(f"Failed to parse metadata: {e}")

    # Reduced logging to improve performance

    # Role-specific instructions and voice
    if persona == "Halima":
        voice_name = "en-US-JennyNeural" # Azure Voice
        instructions = f"""You are Halima, a Kenyan farmer selling bulk maize. 
CRITICAL: This is a realtime voice conversation. Keep responses very brief (1-2 sentences).
NEGOTIATION RULES:
1. MANDATORY: You MUST confirm the specific quantity (in kg or tons) before agreeing to anything.
2. If the buyer doesn't mention quantity, ask: "How many kilograms are you looking for?"
3. Do not mention paperwork or contracts until Price, Quantity, and Delivery are all confirmed.
4. Once settled, say "I'll get the paperwork ready" or "I'll send the contract".
5. You are speaking with Alex.
"""
    else:
        voice_name = "en-US-GuyNeural" # Azure Voice
        instructions = f"""You are Alex, a professional commodity buyer.
CRITICAL: This is a realtime voice conversation. Keep responses very brief (1-2 sentences).
NEGOTIATION RULES:
- Target: $1.15/kg, Maximum: $1.25/kg.
- Discuss delivery and payment terms before agreeing.
- You are speaking with Halima.
"""

    await ctx.connect()
    # Set participant metadata so the frontend can robustly identify the agent persona
    await ctx.room.local_participant.set_metadata(json.dumps({"persona": persona}))
    
    # Create AgentSession with stable settings from working sync-agents baseline
    session_inner = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=azure.TTS(voice=voice_name),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(), # min_endpointing_delay can be tuned here if SDK supports
        resume_false_interruption=False,
        false_interruption_timeout=0.0,
    )
    # Initialize the agent explicitly so we can refer to it in listeners
    negotiation_agent = NegotiationAgent(instructions, persona, ctx)

    # Assign to module-level placeholders for testability/background-task access
    session = session_inner
    current_worker_id = f"[{persona.upper()}-{os.getpid()}]"
    print(f"DEBUG: 🛠️  {current_worker_id} Starting agent session for {persona}")

    async def broadcast_data_inner(data: dict, reliable: bool = True):
        """Helper to broadcast data to all participants in the room"""
        try:
            payload = json.dumps(data).encode('utf-8')
            await ctx.room.local_participant.publish_data(payload, reliable=reliable)
        except Exception as e:
             logger.error(f"{current_worker_id} Error broadcasting {data.get('type')}: {e}")
    
    broadcast_data = broadcast_data_inner

    # BROADCASTERS for cross-browser sync
    @session.on("agent_state_changed")
    def on_agent_state_changed(event: AgentStateChangedEvent):
        # Sync the "Speaking" status for waveforms (Audio Form Syncing)
        asyncio.create_task(broadcast_data({
            "type": "SPEECH_STATE",
            "agent": persona,
            "state": event.new_state,
            "is_speaking": event.new_state == "speaking"
        }))

        # Send a tactical thought when the agent starts analyzing the conversation
        if event.new_state == "thinking" and not negotiation_agent.is_awaiting_approval:
            tactical_thoughts = {
                "Halima": [
                    "Analyzing market demand. Must justify the $1.25 premium.",
                    "Evaluating buyer's tone. He seems interested in quality.",
                    "Calculating transport costs vs. final sale price.",
                    "Staying firm on the minimum. Maize quality is at its peak."
                ],
                "Alex": [
                    "Scanning for budget overruns. Target is still $1.15.",
                    "Checking competitor prices. Halima's maize looks superior.",
                    "Negotiating payment terms - 7 days cash is preferred.",
                    "Wondering if volume discount is an option."
                ]
            }
            thought = random.choice(tactical_thoughts.get(persona, ["Analyzing current data..."]))
            asyncio.create_task(broadcast_data({
                "type": "thought",
                "agent": persona,
                "text": thought
            }))

    # Removed redundant on_user_transcript broadcast to prevent multi-agent 'he-said/she-said' duplication.
    # We rely on each speaker (agent or human) to broadcast/publish their own transcripts.

    # Note: on_user_turn_completed is handled by the NegotiationAgent class method at line 58.
    # No redundant session listener required here.

    @session.on("conversation_item_added")
    def on_conversation_item(event: ConversationItemAddedEvent):
        role = event.item.role
        text = (event.item.text_content or "").strip()
        print(f"DEBUG: 📥 {current_worker_id} [ITEM ADDED] {role}: {text[:50]}...")

        # 1. BRAODCAST ASSISTANT SPEECH
        if role == "assistant":
            if text:
                asyncio.create_task(broadcast_data({
                    "type": "SPEECH",
                    "text": text,
                    "speaker": persona,
                    "is_final": True
                }))

                # 2. HALIMA INTENT DETECTION (Closing the deal)
                if persona == "Halima":
                    # Even broader regex to catch 'set', 'deal', 'finalize', etc.
                    intent_pattern = r"(paperwork|contract|agreement|paperwork ready|send.*contract|formalize.*agreement|finalize.*deal|sign.*paperwork|ready.*paperwork|get.*paperwork|finalize.*details|we're set|sounds like a deal)"
                    match = re.search(intent_pattern, text.lower())
                    if match:
                        print(f"DEBUG: ✨ [INTENT MATCH] Found '{match.group(0)}' in Halima speech")
                        if not negotiation_agent.is_awaiting_approval:
                            print(f"DEBUG: ✅ [TRIGGER] Calling extract_and_preview for {persona}")
                            negotiation_agent.is_awaiting_approval = True
                            session.interrupt()
                            asyncio.create_task(extract_and_preview(negotiation_agent, session, persona, current_worker_id, broadcast_data))
                        else:
                            print(f"DEBUG: ⏭️ [SKIP] Already awaiting approval (state: {negotiation_agent.is_awaiting_approval})")
                    else:
                        print(f"DEBUG: 🚫 [NO MATCH] Speech did not contain closing intent.")

        # 3. USER INTENT FALLBACK (If Alex says "send paperwork")
        elif role == "user" and persona == "Halima":
            if text:
                if re.search(r"(send.*contract|finalize.*deal|sign.*paperwork|ready.*paperwork|get.*paperwork|finalize.*details|we're set|sounds like a deal)", text.lower()):
                    print(f"DEBUG: 💡 [USER INTENT] Detected deal closure from user: '{text[:30]}'")
                    if not negotiation_agent.is_awaiting_approval:
                        print(f"DEBUG: ✅ [TRIGGER] Calling extract_and_preview (USER FALLBACK)")
                        negotiation_agent.is_awaiting_approval = True
                        session.interrupt()
                        asyncio.create_task(extract_and_preview(negotiation_agent, session, persona, current_worker_id, broadcast_data))

    # Data Packet Listener for State Sync (Agent's internal history sync)
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket):
        if not dp.data:
            return
        
        try:
            data = json.loads(dp.data.decode())
            
            # Handle SYNC_REQUEST from newly joined browsers
            if data.get("type") == "SYNC_REQUEST":
                print(f"DEBUG: 📥 {current_worker_id} received SYNC_REQUEST")
                return

            print(f"DEBUG: 📥 {current_worker_id} data_received raw: {data}")
            print(f"DEBUG: 📥 {current_worker_id} Packet Recvd: {data.get('type')}")
            
            # SILENT APPROVAL SYNC: Pause speech if drafting/previewing
            if data.get("type") in ["CONTRACT_INTENT", "CONTRACT_PREVIEW"]:
                print(f"DEBUG: 🤫 {current_worker_id} silencing for contract flow. (Awaiting Approval: TRUE)")
                session.interrupt()
                negotiation_agent.is_awaiting_approval = True
                return

            if data.get("type") == "CONTRACT_APPROVED":
                print(f"DEBUG: ✅ {persona} Contract Approved signal received. (Awaiting Approval: FALSE)")
                negotiation_agent.is_awaiting_approval = False
                
                # SENDER SIDE (Halima) - Finalize and Share
                if persona == "Halima":
                    asyncio.create_task(broadcast_data({
                        "type": "FILE_SHARED",
                        "from": persona,
                        "filename": "maize_supply_contract_final.pdf",
                        "url": "#", # Simulated URL
                        "contract_data": negotiation_agent.pending_contract_data
                    }))
                    
                    # Acknowledge verbally
                    asyncio.create_task(session.generate_reply(
                        instructions="The user has approved the contract. Tell the buyer you have sent the final document and thank them.",
                        allow_interruptions=False
                    ))

            elif data.get("type") == "CONTRACT_REJECTED":
                print(f"DEBUG: ❌ {persona} Contract Rejected. Resetting state...")
                negotiation_agent.is_awaiting_approval = False
                
                # SENDER SIDE (Halima) - Acknowledge feedback
                if persona == "Halima":
                    asyncio.create_task(session.generate_reply(
                        instructions=f"The user rejected the contract draft with this feedback: '{data.get('reason')}'. Acknowledge this and ask how to proceed.",
                        allow_interruptions=False
                    ))

            # SHARED RESPONSE (Both reset on file receipt)
            elif data.get("type") == "FILE_SHARED":
                print(f"DEBUG: 📥 {persona} sees file shared. (Awaiting Approval: FALSE)")
                negotiation_agent.is_awaiting_approval = False
                
                if persona == "Alex":
                    asyncio.create_task(session.generate_reply(
                        instructions="You just received the final contract from Halima. Tell her you see it and it looks perfect.",
                        allow_interruptions=False
                    ))
        except Exception as e:
            logger.error(f"Error in data listener: {e}")

    @ctx.room.on("transcription_received")
    def on_transcription_received(transcription: rtc.Transcription, participant: rtc.RemoteParticipant = None, publication: rtc.TrackPublication = None):
        """Robust handler for transcriptions (Handles both object and raw list shapes)"""
        try:
            # Prefer first segment text, support list or object shapes
            if isinstance(transcription, list):
                first = transcription[0] if transcription else None
            else:
                first = (transcription.segments[0] if getattr(transcription, "segments", None) else None)
            
            # Extract text safely from dict or object
            msg = getattr(first, "text", None) or (first.get("text") if isinstance(first, dict) else (str(first) if first is not None else ""))
            
            if participant and msg:
                print(f"DEBUG: 📝 {current_worker_id} Transcript Recvd from {participant.identity or 'unknown'}: {msg[:30]}...")
        except Exception:
            pass

    # Start session
    logger.info(f"🎙️ Starting {persona} agent session (Identity: {ctx.room.local_participant.identity})")
    await session.start(
        agent=negotiation_agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda p: noise_cancellation.BVC()
            ),
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_AGENT],
        ),
    )

    # Only one agent should proactively speak
    is_initiator = persona == "Halima"

    if is_initiator and "call-" in ctx.room.name:
        await asyncio.sleep(2)
        logger.info(f"{persona} is the initiator, making opening offer")
        await session.generate_reply(
            instructions="Introduce yourself briefly and make your opening offer of $1.25/kg.",
            allow_interruptions=False,
        )

    # Simple keep-alive loop
    while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        await asyncio.sleep(1)

# -------------------------------------------------
# CLI Runner
# -------------------------------------------------
from livekit.agents import WorkerOptions

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="negotiation-worker",
        )
    )
