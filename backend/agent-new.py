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
        self.current_offer = None
        self.deal_finalized = False
        self.round = 1
        self.max_rounds = 15

    @llm.function_tool
    async def propose_offer(
        self,
        price: Annotated[float, Field(description="USD per kg")],
        delivery_included: Annotated[bool, Field(description="Whether delivery is included")],
        transport_paid_by: Annotated[str, Field(description="seller or buyer")],
        payment_terms: Annotated[str, Field(description="cash, 7_days, or 14_days")],
    ) -> str:
        """Tool for agents to propose a concrete multi-lever offer. Call this when you want to make or update an offer."""
        offer = {
            "price": round(price, 2),
            "delivery_included": delivery_included,
            "transport_paid_by": transport_paid_by,
            "payment_terms": payment_terms,
        }
        self.current_offer = offer
        self.round = min(self.round + 1, self.max_rounds)
        
        logger.info(f"📦 [OFFER] {self.persona} (Round {self.round}): {offer}")
        
        try:
            # Broadcast offer update
            await self.ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "offer_update",
                    "agent": self.persona,
                    "offer": offer,
                }).encode(),
                reliable=True
            )
            # Also broadcast timeline update to sync progress bar
            await self.ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "negotiation_timeline",
                    "turn": self.persona,
                    "round": self.round,
                    "max_rounds": self.max_rounds,
                }).encode(),
                reliable=True
            )
            return f"Offer published successfully for round {self.round}."
        except Exception as e:
            logger.error(f"❌ Failed to publish offer: {e}")
            return f"Error publishing offer: {e}"

    @llm.function_tool
    async def finalize_deal(
        self,
        accepted_offer_summary: Annotated[str, Field(description="Brief summary of the offer you are accepting")]
    ) -> str:
        """Call this ONLY when you have reached a final agreement and both parties have verbally accepted."""
        try:
            await self.ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "DEAL_FINALIZED",
                    "agent": self.persona,
                    "summary": accepted_offer_summary,
                }).encode()
            )
            return "Deal finalization signal sent."
        except Exception as e:
            return f"Error: {e}"

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
- Use the propose_offer tool whenever you make a concrete offer.
- You must reach a deal within about 8 exchanges.
- If the offer is good, say acceptance verbally AND call finalize_deal.
- You are speaking with {('Alex' if persona == 'Halima' else 'Halima')}.
"""
    else:
        voice_name = "en-US-GuyNeural" # Azure Voice
        instructions = f"""You are Alex, a professional commodity buyer.
CRITICAL: This is a realtime voice conversation. Keep responses very brief (1-2 sentences).
NEGOTIATION RULES:
- Target: $1.15/kg, Maximum: $1.25/kg.
- Use the propose_offer tool whenever you make a concrete offer.
- You must reach a deal within about 8 exchanges.
- If the offer is good, say acceptance verbally AND call finalize_deal.
- You are speaking with {('Alex' if persona == 'Halima' else 'Halima')}.
"""

    await ctx.connect()

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
        
        # When agent finishes speaking, increment round and broadcast timeline
        if event.old_state == "speaking" and event.new_state != "speaking":
            negotiation_agent.round = min(negotiation_agent.round + 1, negotiation_agent.max_rounds)
            asyncio.create_task(broadcast_data({
                "type": "negotiation_timeline",
                "turn": persona,
                "round": negotiation_agent.round,
                "max_rounds": negotiation_agent.max_rounds,
                "progress": (negotiation_agent.round / negotiation_agent.max_rounds) * 100
            }))

    @session.on("user_input_transcribed")
    def on_user_transcript(event: UserInputTranscribedEvent):
        if event.transcript.strip():
            # Broadcast the user's transcript to other screens
            asyncio.create_task(broadcast_data({
                "type": "SPEECH",
                "text": event.transcript,
                "speaker": "Buyer" if persona == "Halima" else "Seller", # The person the agent hears
                "is_final": event.is_final
            }))

    @session.on("conversation_item_added")
    def on_conversation_item(event: ConversationItemAddedEvent):
        # Broadcast the agent's OWN final transcripts and thoughts
        if event.item.type == "message" and event.item.role == "assistant":
            text = event.item.text_content
            if text and text.strip():
                # Redundant Speech Sync
                asyncio.create_task(broadcast_data({
                    "type": "SPEECH",
                    "text": text,
                    "speaker": persona,
                    "is_final": True
                }))
                # Broadcast first part as thought
                asyncio.create_task(broadcast_data({
                    "type": "thought",
                    "agent": persona,
                    "text": f"Finalizing my response: '{text[:50]}...'"
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
                # 1. Sync Offer
                if negotiation_agent.current_offer:
                     logger.info(f"📤 {persona} broadcasting sync offer: {negotiation_agent.current_offer}")
                     asyncio.create_task(broadcast_data({
                         "type": "offer_update",
                         "agent": persona,
                         "offer": negotiation_agent.current_offer
                     }))
                # 2. Sync Timeline
                logger.info(f"📤 {persona} broadcasting sync timeline: Round {negotiation_agent.round}")
                asyncio.create_task(broadcast_data({
                    "type": "negotiation_timeline",
                    "turn": persona,
                    "round": negotiation_agent.round,
                    "max_rounds": negotiation_agent.max_rounds,
                    "progress": (negotiation_agent.round / negotiation_agent.max_rounds) * 100
                }))
                return

            if data.get("type") == "offer_update" and data.get("agent") != persona:
                other_agent = data.get("agent")
                offer = data.get("offer")
                logger.info(f"🔄 {persona} syncing state: {other_agent} offered {offer}")
                session.history.add_message(
                    role="system",
                    content=f"{other_agent} has proposed a concrete offer: {offer}. You can now respond to these specific terms."
                )
            elif data.get("type") == "DEAL_FINALIZED" and data.get("agent") != persona:
                session.history.add_message(
                    role="system",
                    content=f"{data.get('agent')} has accepted the deal and finalized it. You should now conclude the call politely."
                )
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
