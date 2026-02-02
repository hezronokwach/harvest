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
from livekit.plugins import silero, noise_cancellation, deepgram, groq, hume
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

# -------------------------------------------------
# Shared state
# -------------------------------------------------
STATE = {
    "rounds": 0,
    "max_rounds": 8,
    "sessions": {},
}

# -------------------------------------------------
# Agent
# -------------------------------------------------
class NegotiationAgent(Agent):
    def __init__(self, instructions: str):
        super().__init__(instructions=instructions)

# -------------------------------------------------
# Server
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
    agent_name = ctx.job.agent_name

    # Resolve persona
    if agent_name == "negotiation-worker" and ctx.job.metadata:
        import json
        meta = json.loads(ctx.job.metadata)
        agent_name = "halima-agent" if meta["persona"] == "Juma" else "alex-agent"

    logger.info(f"Starting {agent_name}")

    # Personas
    if agent_name == "halima-agent":
        instructions = """You are Halima, a Kenyan farmer selling bulk maize.

NEGOTIATION DIMENSIONS:
- Price per kg: Start at $1.25/kg, minimum $1.10/kg
- Delivery: You can include delivery if buyer covers transport costs
- Transport: Buyer should pay transport costs
- Payment Terms: You prefer 14-day payment (can accept 7 days for better price)
- Logistics: You can deliver within 50km of your farm

STRATEGY:
- Start firm at $1.25/kg, make gradual concessions
- Defend pricing with real costs (fertilizer, labor, fuel)
- Be warm and practical
- Speak naturally and briefly (1-2 sentences)
- Mention specific terms when discussing deals

Example: "I can do $1.20 per kg if you handle transport and pay within 7 days."
"""
    else:
        instructions = """You are Alex, a professional commodity buyer purchasing maize.

NEGOTIATION DIMENSIONS:
- Price per kg: Target $0.90/kg, maximum $1.35/kg
- Delivery: You want delivery included
- Transport: Seller should cover transport
- Payment Terms: You prefer 7-day payment (can do 14 days for lower price)
- Logistics: Need delivery to warehouse in Nairobi

STRATEGY:
- Start low, push for favorable terms
- Evaluate total deal (price + delivery + payment), not just price
- Point out market conditions to justify lower prices
- Be analytical and concise
- Speak naturally and briefly (1-2 sentences)

Example: "I can offer $1.20 per kg if you include delivery and I pay in 14 days."
"""

    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
      tts=hume.TTS(
    voice=hume.VoiceByName(
        name="Kora" if agent_name == "halima-agent" else "Big Dicky",
        provider="HUME_AI",  # Use string literal instead
    ),
    instant_mode=True,
),
    vad=ctx.proc.userdata["vad"],
)

    await session.start(
        agent=NegotiationAgent(instructions),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda p:
                noise_cancellation.BVC()
            ),
            participant_kinds=[
                rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
            ],
        ),
    )

    STATE["sessions"][agent_name] = session
    logger.info(f"Session ready: {agent_name}")

    # -------------------------------------------------
    # TURN CHAINING (THIS IS THE IMPORTANT PART)
    # -------------------------------------------------

    async def halima_after_speech(text: str):
        STATE["rounds"] += 1
        logger.info(f"[ROUND {STATE['rounds']}] Halima finished")

        if STATE["rounds"] >= STATE["max_rounds"]:
            await session.generate_reply(
                instructions="Summarize the deal and say goodbye.",
                allow_interruptions=False,
            )
            return

        await STATE["sessions"]["alex-agent"].generate_reply(
            instructions=f"Halima just said: '{text}'\n\nRespond naturally. Discuss price, delivery, payment terms, and logistics.",
            allow_interruptions=False,
        )

    async def alex_after_speech(text: str):
        await STATE["sessions"]["halima-agent"].generate_reply(
            instructions=f"Alex just said: '{text}'\n\nRespond naturally. Discuss price, delivery, payment terms, and logistics.",
            allow_interruptions=False,
        )

    # Attach handlers
    if agent_name == "halima-agent":
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(halima_after_speech(text))
        )
    else:
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(alex_after_speech(text))
        )

    # -------------------------------------------------
    # START CONVERSATION
    # -------------------------------------------------
    if agent_name == "halima-agent":
        await session.generate_reply(
            instructions="Start the negotiation. Introduce yourself briefly and state your initial offer including price, delivery terms, and payment expectations. Keep it natural and brief.",
            allow_interruptions=False,
        )

    # -------------------------------------------------
    # Keep alive
    # -------------------------------------------------
    while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
        await asyncio.sleep(1)

# -------------------------------------------------
# Run
# -------------------------------------------------
from livekit.agents import WorkerOptions

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="negotiation-worker",
            load_threshold=1.2,  # ✅ allow higher CPU load before throttling
        )
    )