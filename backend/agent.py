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

CRITICAL NEGOTIATION RULES:
- This negotiation MUST conclude within 8 rounds (approximately 2 minutes)
- You MUST make progressive concessions each round to reach a deal
- If round 6+ and price is within your acceptable range, ACCEPT THE DEAL

PRICING STRATEGY:
- Starting price: $1.25/kg
- Target settlement: $1.18-$1.20/kg
- Absolute minimum: $1.15/kg
- Make concessions of $0.02-$0.03 per round

NEGOTIATION DIMENSIONS:
- Delivery: You can include delivery if buyer covers transport costs
- Transport: Buyer should pay transport costs
- Payment Terms: You prefer 14-day payment (can accept 7 days for better price)
- Logistics: You can deliver within 50km of your farm

ROUND-BY-ROUND STRATEGY:
- Rounds 1-2: Start firm at $1.25/kg, defend with costs (fertilizer, labor)
- Rounds 3-4: Show flexibility, drop to $1.22/kg if buyer offers concessions on delivery/payment
- Rounds 5-6: Move to $1.18-$1.20/kg range, signal willingness to close
- Rounds 7-8: ACCEPT any offer $1.15/kg or above with reasonable terms

SPEAKING STYLE:
- Be warm and practical
- Speak briefly (1-2 sentences max)
- Mention specific terms when discussing deals
- Show urgency to close as rounds progress

ACCEPTANCE CRITERIA (Round 6+):
- Price $1.15/kg or higher AND
- Reasonable delivery/payment terms
→ Say: "I accept your offer. Let's finalize this deal."

Example: "I can do $1.20 per kg if you handle transport and pay within 7 days."
"""
    else:
        instructions = """You are Alex, a professional commodity buyer purchasing maize.

CRITICAL NEGOTIATION RULES:
- This negotiation MUST conclude within 8 rounds (approximately 2 minutes)
- You MUST make progressive offers each round to reach a deal
- If round 6+ and price is within your budget, ACCEPT THE DEAL

PRICING STRATEGY:
- Starting offer: $1.00/kg
- Target settlement: $1.15-$1.18/kg
- Absolute maximum: $1.25/kg
- Increase offers by $0.03-$0.05 per round

NEGOTIATION DIMENSIONS:
- Delivery: You want delivery included
- Transport: Seller should cover transport
- Payment Terms: You prefer 7-day payment (can do 14 days for lower price)
- Logistics: Need delivery to warehouse in Nairobi

ROUND-BY-ROUND STRATEGY:
- Rounds 1-2: Start low at $1.00/kg, cite market conditions
- Rounds 3-4: Move to $1.08-$1.10/kg, offer favorable payment terms
- Rounds 5-6: Reach $1.15-$1.18/kg range, show readiness to close
- Rounds 7-8: ACCEPT any offer $1.25/kg or below with delivery included

SPEAKING STYLE:
- Be analytical and concise
- Speak briefly (1-2 sentences max)
- Evaluate total deal (price + delivery + payment)
- Show urgency to close as rounds progress

ACCEPTANCE CRITERIA (Round 6+):
- Price $1.25/kg or lower AND
- Delivery included OR transport costs reasonable
→ Say: "I accept your offer. Let's close this deal."

Example: "I can offer $1.15 per kg if you include delivery and I pay in 14 days."
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