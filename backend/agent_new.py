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
     - session_obj.history (Official SDK context property) [PHASE 17 FIX]
     - session_obj.chat_ctx (if present)
     - fallback value
    """
    if hasattr(agent_obj, "last_chat_ctx") and agent_obj.last_chat_ctx:
        return agent_obj.last_chat_ctx
    if hasattr(agent_obj, "chat_ctx") and getattr(agent_obj, "chat_ctx"):
        return agent_obj.chat_ctx
    if hasattr(session_obj, "history") and session_obj.history:
        return session_obj.history
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

class ContractExtractionContext: 
    """Context for LLM to submit structured agreement terms."""
    
    def __init__(self):
        super().__init__()
        self.extracted_data = {}

    @llm.function_tool
    def submit_terms(
        self,
        buyer: str = "",
        product: str = "",
        price: str = "",
        quantity: str = "",
        delivery: str = "",
        payment: str = ""
    ):
        """Submit final negotiated terms for the maize supply contract.
        
        Args:
            buyer: Name of the buyer (e.g. Alex)
            product: Product being sold (e.g. White Maize)
            price: Negotiated price (e.g. $1.20/kg)
            quantity: Total quantity (e.g. 5 tons)
            delivery: Delivery location or terms
            payment: Payment details (e.g. Mobile Money, 50% upfront)
        """
        # Idempotent merging: Keep last non-empty value for each field
        inc = {
            "buyer": str(buyer or "").strip(),
            "product": str(product or "").strip(),
            "price": str(price or "").strip(),
            "quantity": str(quantity or "").strip(),
            "delivery": str(delivery or "").strip(),
            "payment": str(payment or "").strip()
        }
        for k, v in inc.items():
            if v:
                self.extracted_data[k] = v
        
        logger.info("✅ Tool Call Received (Idempotent Merge): %s", self.extracted_data)

async def extract_and_preview(agent, session, persona, worker_id, broadcast_data):
    """Trigger LLM Term Extractor (Phase 17 Reliability)"""
    global llm_client
    
    # [PHASE 17]: Persona Guard - Only Halima triggers extraction
    if persona != "Halima":
        logger.debug("%s Skipping extract_and_preview: Unsupported persona", worker_id)
        return

    # 1. IMMEDIATE Feedback
    logger.info(f"{worker_id} 📤 Broadcasting CONTRACT_INTENT")
    await broadcast_data({
        "type": "CONTRACT_INTENT",
        "agent": persona,
        "status": "drafting"
    })

    try:
        # 2. Resolve History with Fallback
        raw_ctx = resolve_chat_ctx(agent, session)
        history_messages = normalize_chat_ctx(raw_ctx)
        
        if not history_messages:
            # FALLBACK: Use latest transcript if history window is empty
            last_spoken = ""
            if hasattr(session, "last_transcript") and session.last_transcript:
                last_spoken = session.last_transcript
            
            history_text = truncate_text(last_spoken or "No prior messages available.", 500)
            logger.warning(f"{worker_id} History empty. Using fallback: '{history_text[:50]}...'")
        else:
            raw_history = "\n".join([f"{m.role}: {m.content}" for m in history_messages])
            history_text = truncate_text(raw_history, 2500)
        
        sanitized_history = history_text.replace("slice(", "[SLICE_REPR]")

        # 3. Extraction with Retry Logic
        fnc_ctx = ContractExtractionContext()
        extracted_data = None
        
        for attempt in range(1, 3):
            logger.info(f"{worker_id} Extraction Attempt {attempt}/2...")
            
            # Instruction Hardening
            system_instruction = (
                "You are a specialized Term Extractor for Kenyan maize deals. Analyze the history and call 'submit_terms'. "
                "You MUST call 'submit_terms' EXACTLY once with all fields. Use empty string \"\" for unknown fields."
            )
            if attempt > 1:
                system_instruction += " CRITICAL: Previous attempt failed. You MUST call submit_terms now."

            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": f"History:\n{sanitized_history}\nExtract terms from the above."}
            ]
            
            llm_messages = [llm.ChatMessage(role=m["role"], content=normalize_content_for_llm(m["content"], worker_id)) for m in messages]

            # Provision client if needed
            if llm_client is None:
                llm_client = groq.LLM(model="llama-3.3-70b-versatile")

            chat = llm_client.chat(
                chat_ctx=llm.ChatContext(items=llm_messages),
                tools=[fnc_ctx.submit_terms]
            )

            async for _ in chat: # Consume stream
                pass

            if fnc_ctx.extracted_data:
                extracted_data = fnc_ctx.extracted_data
                logger.info(f"{worker_id} Extraction succeeded on attempt {attempt}")
                break
            
            if attempt < 2:
                logger.warning(f"{worker_id} Extraction failed on attempt {attempt}. Retrying...")
                await asyncio.sleep(0.5)

        if not extracted_data:
            logger.error(f"{worker_id} Term extraction yielded empty data after retries.")
            await broadcast_data({
                "type": "CONTRACT_PREVIEW_ERROR",
                "agent": persona,
                "error": "EMPTY_EXTRACTION",
                "message": "I couldn't catch the deal details clearly. Please mention price and quantity again."
            })
            agent.is_awaiting_approval = False
            return

        # 4. Populate and Broadcast Preview
        defaults = {
            "buyer": "Alex", "product": "Maize", "price": "Negotiated",
            "quantity": "Negotiated", "delivery": "Discussed", "payment": "Discussed"
        }
        agent.pending_contract_data = {**defaults, **extracted_data}
        
        preview_payload = {
            "type": "CONTRACT_PREVIEW",
            "contract_id": f"ctr_{random.getrandbits(16)}_{persona}",
            "agent": persona,
            "contract_data": agent.pending_contract_data,
            "title": "Maize Supply Agreement (Draft)"
        }
        await broadcast_data(preview_payload)

    except Exception as e:
        logger.error(f"{worker_id} Critical Error in Term Extraction: {e}", exc_info=True)
        err_text = str(e)[:500].replace("slice(", "[SLICE_REPR]")
        await broadcast_data({
            "type": "CONTRACT_PREVIEW_ERROR",
            "agent": persona,
            "error": "FATAL_ERROR",
            "message": f"Deep analysis failed: {err_text[:100]}"
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
        # [PHASE 17 DEBUG]
        logger.debug("%s captured history turn: %d messages in context", self.persona, len(turn_ctx.items))
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

                        print(f"DEBUG: 🚫 [NO MATCH] Speech did not contain closing intent.")

        # 3. USER INTENT FALLBACK - [REMOVED IN PHASE 16]
        # We now only trigger extraction if Halima (the seller) explicitly offers the paperwork.
        # This prevents accidental triggers from the buyer's speech.

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
