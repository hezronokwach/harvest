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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("negotiation-agent")

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
        
        logger.info(f"📦 [OFFER] {self.persona}: {offer}")
        
        try:
            await self.ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "offer_update",
                    "agent": self.persona,
                    "offer": offer,
                }).encode(),
                reliable=True
            )
            return "Offer published successfully and displayed on their screens."
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
                }).encode(),
                reliable=True
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

    logger.info(f"Starting {persona} ({role}) in room {ctx.room.name}")

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

    # Create AgentSession with natural turn detection and echo prevention
    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=azure.TTS(voice=voice_name),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        resume_false_interruption=False,
        false_interruption_timeout=0.0,
    )

    # Data Packet Listener for State Sync
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket):
        if not dp.data:
            return
        
        try:
            data = json.loads(dp.data.decode())
            if data.get("type") == "offer_update" and data.get("agent") != persona:
                other_agent = data.get("agent")
                offer = data.get("offer")
                logger.info(f"🔄 {persona} syncing state: {other_agent} offered {offer}")
                # Update LLM context with a system message
                session.chat_ctx.append(
                    text=f"SYSTEM: {other_agent} has proposed a concrete offer via tool: {offer}. You can now respond to these specific terms.",
                    role="system"
                )
            elif data.get("type") == "DEAL_FINALIZED" and data.get("agent") != persona:
                session.chat_ctx.append(
                    text=f"SYSTEM: {data.get('agent')} has accepted the deal and finalized it. You should now conclude the call politely.",
                    role="system"
                )
        except Exception as e:
            logger.error(f"Error in data listener: {e}")

    # Start session
    await session.start(
        agent=NegotiationAgent(instructions, persona, ctx),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda p: noise_cancellation.BVC()
            ),
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_AGENT],
        ),
    )

    # Only one agent should proactively speak; the other must wait for a user turn.
    # This is the recommended pattern from LiveKit to prevent both agents from speaking simultaneously.
    is_initiator = persona == "Halima"

    if is_initiator and "call-" in ctx.room.name:
        # Wait a moment for other participants to join
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
