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

def negotiation_has_ended(text: str) -> bool:
        keywords = [
            "deal accepted",
            "we have a deal",
            "agreed price",
            "let's proceed",
            "no deal",
            "cannot agree",
            "walk away"
        ]
        text_lower = text.lower()
        return any(k in text_lower for k in keywords)
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
    "You are Halima, a hardworking Kenyan maize farmer negotiating with respect and dignity. "
    "You have 100 bags of high-quality maize to sell at a fair starting price of $1.25 per kilogram. "
    "NEGOTIATION STYLE: "
    "- Be warm, respectful, and professional at all times "
    "- Use tactical empathy: acknowledge Alex’s concerns before defending your price "
    "- Start firm, but show flexibility in small steps (e.g., $1.20, then $1.15) "
    "- Reference your real costs: labor, fertilizer, transport, and storage "
    "- Never be rude, dismissive, or emotional "
    "- Keep responses concise (2–3 sentences maximum) "
    "GOAL: Reach a fair deal between $1.10 and $1.20 per kilogram while maintaining mutual respect."
)
    else:
       instructions = (
    "You are Alex, a professional commodity buyer negotiating on behalf of your company. "
    "You are under budget pressure and aim to buy maize at $0.90 to $1.00 per kilogram. "
    "NEGOTIATION STYLE: "
    "- Be respectful and professional at all times "
    "- Use tactical empathy: acknowledge Halima’s quality and effort before countering "
    "- Start low, but remain realistic about market prices "
    "- Reference budget limits, logistics, and competitive suppliers "
    "- Show willingness to meet in the middle if quality and reliability are clear "
    "- Keep responses concise (2–3 sentences maximum) "
    "GOAL: Reach a deal between $1.00 and $1.15 per kilogram while building a good long-term relationship."
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
        logger.info(f"[ROUND {STATE['rounds']}] Halima finished")

        # Notify frontend of round update
        try:
            await ctx.room.local_participant.publish_data(
                json.dumps({
                    "type": "round_update",
                    "round": STATE["rounds"]
                })
            )
        except Exception as e:
            logger.warning(f"Failed to publish round update: {e}")

        # ✅ Natural ending
        if negotiation_has_ended(text) or STATE["rounds"] >= STATE["max_rounds"]:
            await session.generate_reply(
                instructions=(
                    "Politely summarize the outcome of the negotiation in one sentence "
                    "and end the conversation respectfully. Say goodbye."
                ),
                allow_interruptions=False,
            )
            # Wait for the final message to be spoken
            await asyncio.sleep(3)
            
            # Close both sessions and disconnect
            logger.info("Negotiation ended. Closing sessions...")
            for agent_session in STATE["sessions"].values():
                try:
                    await agent_session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
            
            # Disconnect from room
            await ctx.room.disconnect()
            return

        await STATE["sessions"]["alex-agent"].generate_reply(
            instructions=f"Respond respectfully to Halima:\n{text}",
            allow_interruptions=False,
        )

    async def alex_after_speech(text: str):
        if negotiation_has_ended(text):
            logger.info("Deal reached! Ending negotiation...")
            # Wait a moment then close
            await asyncio.sleep(2)
            
            # Close both sessions
            for agent_session in STATE["sessions"].values():
                try:
                    await agent_session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
            
            # Disconnect from room
            await ctx.room.disconnect()
            return

        await STATE["sessions"]["juma-agent"].generate_reply(
            instructions=f"Respond respectfully to Alex:\n{text}",
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
        instructions=(
            "Greet Alex politely and state your starting price, "
            "while expressing openness to a fair discussion."
        ),
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