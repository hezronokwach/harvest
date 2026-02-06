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
    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=azure.TTS(voice=voice_name),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        resume_false_interruption=False,
        false_interruption_timeout=0.0,
    )
    # Initialize the agent explicitly so we can refer to it in listeners
    negotiation_agent = NegotiationAgent(instructions, persona, ctx)

    # Identifiable prefix for all logs to catch job stealing
    worker_id = f"[{persona.upper()}-{os.getpid()}]"
    print(f"DEBUG: 🛠️  {worker_id} Starting agent session for {persona}")

    async def broadcast_data(data: dict, reliable: bool = True):
        """Helper to broadcast data to all participants in the room"""
        try:
            payload = json.dumps(data).encode('utf-8')
            await ctx.room.local_participant.publish_data(payload, reliable=reliable)
        except Exception as e:
             logger.error(f"{worker_id} Error broadcasting {data.get('type')}: {e}")

    from types import SimpleNamespace

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

    async def consume_llm_stream(stream):
        """Aggregate chunks from an LLM stream into a single string"""
        text = ""
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            if delta is None: continue
            content = getattr(delta, "content", None)
            if content is None: continue
            # Handle both list[str] and raw string formats
            text += "".join(content) if isinstance(content, (list, tuple)) else str(content)
        return text

    # TRIGGER LLM TERM EXTRACTOR (Phase 8 Reliability)
    async def extract_and_preview():
        print(f"DEBUG: 🚀 {worker_id} [TASK START] extract_and_preview")
        # 1. IMMEDIATE Feedback - Signal "Drafting" status BEFORE LLM call
        logger.warning(f"{worker_id} 📤 Broadcasting CONTRACT_INTENT (Instant Feedback)")
        await broadcast_data({
            "type": "CONTRACT_INTENT",
            "agent": persona,
            "status": "drafting"
        })

        try:
            # Safer history extraction (Using normalizer)
            ctx_to_use = negotiation_agent.last_chat_ctx or session.chat_ctx
            try:
                history_messages = normalize_chat_ctx(ctx_to_use)
            except Exception as e:
                logger.error(f"{worker_id} Error normalizing chat context: {e}")
                history_messages = []

            # If history is empty, provide a hint to the LLM
            if not history_messages:
                history_text = "No prior messages available. Negotiation just started."
            else:
                history_text = "\n".join([f"{m.role}: {m.content}" for m in history_messages])
            
            print(f"DEBUG: 📝 {worker_id} History Context Size: {len(history_messages)} items")

            # Construct messages for extraction (Defensive list-wrapping for Pydantic)
            messages = [
                {"role": "system", "content": "You are a specialized Term Extractor for Kenyan maize deals. Analyze the history and extract: buyer (string), product (string), price (string e.g. '$1.15/kg'), quantity (string e.g. '5 tons'), delivery (string), payment (string). Output ONLY JSON."},
                {"role": "user", "content": f"History:\n{history_text}\nExtract terms from the above."}
            ]
            
            # Use a fast model to avoid rate limits and minimize latency
            llm_client = groq.LLM(model="llama-3.1-8b-instant")
            
            # Form final messages with list-wrapped content for schema compliance
            llm_messages = [llm.ChatMessage(role=m["role"], content=[m["content"]]) for m in messages]
            
            # Start the stream - Groq returns an LLMStream, NOT a direct response
            stream = llm_client.chat(chat_ctx=llm.ChatContext(items=llm_messages))
            content = await consume_llm_stream(stream)
            
            # Simple JSON extraction from model output
            try:
                match = re.search(r"\{.*\}", content, re.DOTALL)
                extracted_data = json.loads(match.group()) if match else {}
            except:
                logger.warning("LLM Term Extraction failed to parse JSON, using contextual defaults.")
                extracted_data = {}

            # Populate missing fields with defaults
            defaults = {
                "buyer": "Alex", "product": "Maize", "price": "Negotiated",
                "quantity": "Negotiated", "delivery": "Discussed", "payment": "Discussed"
            }
            # 5. Broadcast final result
            print(f"DEBUG: 📋 {worker_id} Extracted Data: {extracted_data}")
            negotiation_agent.pending_contract_data = {**defaults, **extracted_data}
            
            await broadcast_data({
                "type": "CONTRACT_PREVIEW",
                "contract_id": f"ctr_{ctx.room.name}_{random.getrandbits(16)}_{persona}",
                "agent": persona,
                "contract_data": negotiation_agent.pending_contract_data,
                "title": "Maize Supply Agreement (Draft)"
            })

        except Exception as e:
            logger.error(f"{worker_id} Critical Error in Term Extraction: {e}")
            await broadcast_data({
                "type": "CONTRACT_PREVIEW_ERROR",
                "agent": persona,
                "error": str(e)
            })
            # Reset silence so they can at least keep talking
            negotiation_agent.is_awaiting_approval = False

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
        print(f"DEBUG: 📥 {worker_id} [ITEM ADDED] {role}: {text[:50]}...")

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
                            asyncio.create_task(extract_and_preview())
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
                        asyncio.create_task(extract_and_preview())

    # Data Packet Listener for State Sync (Agent's internal history sync)
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket):
        if not dp.data:
            return
        
        try:
            data = json.loads(dp.data.decode())
            
            # Handle SYNC_REQUEST from newly joined browsers
            if data.get("type") == "SYNC_REQUEST":
                print(f"DEBUG: 📥 {worker_id} received SYNC_REQUEST")
                return

            print(f"DEBUG: 📥 {worker_id} Packet Recvd: {data.get('type')}")
            
            # SILENT APPROVAL SYNC: Pause speech if drafting/previewing
            if data.get("type") in ["CONTRACT_INTENT", "CONTRACT_PREVIEW"]:
                print(f"DEBUG: 🤫 {worker_id} silencing for contract flow. (Awaiting Approval: TRUE)")
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
                print(f"DEBUG: 📝 {worker_id} Transcript Recvd from {participant.identity or 'unknown'}: {msg[:30]}...")
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
