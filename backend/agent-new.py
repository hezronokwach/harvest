from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    JobProcess,
    cli,
    room_io,
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
- Target: $1.25/kg, Minimum: $1.15/kg.
- Negotiate delivery (included or extra) and payment terms (cash on delivery or 7 days).
- Once terms are settled, say "I'll get the paperwork ready" or "I'll send the contract".
- You are speaking with Alex.
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

    async def broadcast_data(data: dict, reliable: bool = True):
        """Helper to broadcast data to all participants in the room"""
        try:
            payload = json.dumps(data).encode('utf-8')
            await ctx.room.local_participant.publish_data(payload, reliable=reliable)
        except Exception as e:
             logger.error(f"Error broadcasting {data.get('type')}: {e}")

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

    @session.on("conversation_item_added")
    def on_conversation_item(event: ConversationItemAddedEvent):
        # Broadcast the agent's OWN final transcripts
        if event.item.type == "message" and event.item.role == "assistant":
            text = event.item.text_content
            if text and text.strip():
                # Speech Sync: Ensure the transcript appears exactly once in the list
                asyncio.create_task(broadcast_data({
                    "type": "SPEECH",
                    "text": text,
                    "speaker": persona, # Always "Halima" or "Alex"
                    "is_final": True
                }))

                # CONTRACT INTENT DETECTION (Based on contract-n-history.md)
                intent_keywords = ["send the contract", "send you the contract", "draft the agreement", "final contract", "paperwork ready", "paperwork for shipment"]
                if any(kw in text.lower() for kw in intent_keywords) and persona == "Halima":
                    logger.info(f"📝 {persona} Intent Detected: Drafting contract...")
                    negotiation_agent.is_awaiting_approval = True
                    
                    # EXTRACT TERMS (Simulated structured extraction - in a real app this would be an LLM call)
                    # For now, we use the context of the agent's final agreement sentence
                    negotiation_agent.pending_contract_data = {
                        "buyer": "Alex",
                        "product": "Maize",
                        "price": "$1.18/kg", # Defaulting to last mentioned stable price
                        "quantity": "5000kg",
                        "delivery": "Free in Nairobi",
                        "payment": "Cash on delivery"
                    }
                    
                    # 1. Signal "Drafting" status
                    asyncio.create_task(broadcast_data({
                        "type": "CONTRACT_INTENT",
                        "agent": persona,
                        "status": "drafting"
                    }))

                    # 2. Emit CONTRACT_PREVIEW after a short simulate "drafting" delay
                    async def emit_preview():
                        await asyncio.sleep(2)
                        await broadcast_data({
                            "type": "CONTRACT_PREVIEW",
                            "contract_id": f"ctr_{ctx.room.name}_{persona}",
                            "agent": persona,
                            "contract_data": negotiation_agent.pending_contract_data,
                            "title": "Maize Supply Agreement"
                        })
                    
                    asyncio.create_task(emit_preview())

    # Data Packet Listener for State Sync (Agent's internal history sync)
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket):
        if not dp.data:
            return
        
        try:
            data = json.loads(dp.data.decode())
            
            # Handle SYNC_REQUEST from newly joined browsers
            if data.get("type") == "SYNC_REQUEST":
                logger.info(f"📥 {persona} received SYNC_REQUEST from dashboard")
                return

            # CONTRACT APPROVAL FLOW (Human-in-the-loop)
            if data.get("type") == "CONTRACT_APPROVED" and persona == "Halima":
                logger.info(f"✅ {persona} Contract Approved by User.")
                negotiation_agent.is_awaiting_approval = False
                
                # Finalize and Share
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

            elif data.get("type") == "CONTRACT_REJECTED" and persona == "Halima":
                logger.info(f"❌ {persona} Contract Rejected. Feedback: {data.get('reason')}")
                negotiation_agent.is_awaiting_approval = False
                
                # Acknowledge rejection verbally and resume negotiation
                asyncio.create_task(session.generate_reply(
                    instructions=f"The user rejected the contract draft with this feedback: '{data.get('reason')}'. Acknowledge this and ask how to proceed.",
                    allow_interruptions=False
                ))

            # RECIPIENT SIDE RESPONSE
            elif data.get("type") == "FILE_SHARED" and persona == "Alex":
                logger.info(f"📥 {persona} received final contract. Reacting...")
                asyncio.create_task(session.generate_reply(
                    instructions="You just received the final contract from Halima. Tell her you see it and it looks perfect.",
                    allow_interruptions=False
                ))
        except Exception as e:
            logger.error(f"Error in data listener: {e}")

    # Silence 'ignoring text stream' logs
    @ctx.room.on("transcription_received")
    def on_transcription_received(transcription):
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
