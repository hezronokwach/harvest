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
- You are speaking with {('Alex' if persona == 'Halima' else 'Halima')}.
"""
    else:
        voice_name = "en-US-GuyNeural" # Azure Voice
        instructions = f"""You are Alex, a professional commodity buyer.
CRITICAL: This is a realtime voice conversation. Keep responses very brief (1-2 sentences).
NEGOTIATION RULES:
- Target: $1.15/kg, Maximum: $1.25/kg.
- You are speaking with {('Alex' if persona == 'Halima' else 'Halima')}.
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
        if event.new_state == "thinking":
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
                # Maintain basic sync if needed (transcripts etc)
                return
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
