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
        agent_name = "juma-agent" if meta["persona"] == "Juma" else "alex-agent"

    logger.info(f"Starting {agent_name}")

    # Personas
    if agent_name == "juma-agent":
        instructions = (
            "You are Juma, a firm maize farmer. "
            "Sell at no less than $1.15/kg. Speak briefly."
        )
    else:
        instructions = (
            "You are Alex, a tough buyer. "
            "Try to buy at $0.90/kg or less. Speak briefly."
        )

    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
      tts=hume.TTS(
    voice=hume.VoiceByName(
        name="Kora" if agent_name == "juma-agent" else "Big Dicky",
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

    async def juma_after_speech(text: str):
        STATE["rounds"] += 1
        logger.info(f"[ROUND {STATE['rounds']}] Juma finished")

        if STATE["rounds"] >= STATE["max_rounds"]:
            await session.generate_reply(
                instructions="Summarize the deal and say goodbye.",
                allow_interruptions=False,
            )
            return

        await STATE["sessions"]["alex-agent"].generate_reply(
            instructions=f"Respond to Juma:\n{text}",
            allow_interruptions=False,
        )

    async def alex_after_speech(text: str):
        await STATE["sessions"]["juma-agent"].generate_reply(
            instructions=f"Respond to Alex:\n{text}",
            allow_interruptions=False,
        )

    # Attach handlers
    if agent_name == "juma-agent":
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(juma_after_speech(text))
        )
    else:
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(alex_after_speech(text))
        )

    # -------------------------------------------------
    # START CONVERSATION
    # -------------------------------------------------
    if agent_name == "juma-agent":
        await session.generate_reply(
            instructions="State your maize price clearly.",
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