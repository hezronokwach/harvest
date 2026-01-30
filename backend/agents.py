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

import os
from pathlib import Path

if not load_dotenv():
    # If that fails or file missing, try parent dir
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)

if not os.getenv("LIVEKIT_URL") and os.getenv("NEXT_PUBLIC_LIVEKIT_URL"):
    os.environ["LIVEKIT_URL"] = os.getenv("NEXT_PUBLIC_LIVEKIT_URL")
logger = logging.getLogger("negotiation-agent")
logger.setLevel(logging.INFO)

# -----------------------
# Shared base agent
# -----------------------
class NegotiationAgent(Agent):
    def __init__(self, instructions: str):
        super().__init__(instructions=instructions)

# -----------------------
# Agent server
# -----------------------
server = AgentServer()

def prewarm(proc: JobProcess):
    try:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("VAD model prewarmed")
    except Exception as e:
        logger.error(f"Failed to prewarm VAD: {e}")

server.setup_fnc = prewarm

# -----------------------
# Unified Entrypoint
# -----------------------
@server.rtc_session()
async def negotiation_entrypoint(ctx: JobContext):
    agent_name = ctx.job.agent_name
    logger.info(f"Incoming dispatch request. Agent Name: '{agent_name}' in room: {ctx.room.name}")

    if agent_name == "juma-agent":
        instructions = (
            "You are Juma, a protective and firm maize farmer. "
            "Your goal is to sell your harvest for at least $1.15/kg. "
            "You speak with a warm but steady tone. "
            "Defend your price using tactical empathy. Speak concisely."
        )
        voice_name = "Male English Actor"
        thought_text = "Starting negotiation. I need to hold firm at $1.25 to see how Alex reacts."
    elif agent_name == "alex-agent":
        instructions = (
            "You are Alex, a skeptical and hurried commodity buyer. "
            "Your goal is to buy maize for as low as possible, ideally $0.90/kg. "
            "You sound impatient and try to anchor the price low. Speak concisely."
        )
        voice_name = "Kora" # Soft Female Voice
        thought_text = "I will try to anchor the price low at $0.85. Juma looks like he needs the cash."
    else:
        logger.warning(f"Unexpected agent name: '{agent_name}'. Job ID: {ctx.job.id}")
        return

    try:
        # Explicitly connect to the room before starting the session
        await ctx.connect()
        logger.info(f"Connected to room: {ctx.room.name}")

        session = AgentSession(
            stt=deepgram.STT(),
            llm=groq.LLM(model="llama-3.3-70b-versatile"),
            tts=hume.TTS(
                voice=hume.VoiceByName(
                    name=voice_name,
                    provider=hume.VoiceProvider.hume,
                ),
                instant_mode=True,
            ),
            vad=ctx.proc.userdata["vad"],
        )

        await session.start(
            agent=NegotiationAgent(instructions=instructions),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=lambda p:
                    noise_cancellation.BVC()
                    if p.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
                    else noise_cancellation.BVCTelephony(),
                )
            ),
        )

        # Emit an initial thought
        await ctx.room.local_participant.publish_data(
            f'{{"type": "thought", "text": "{thought_text}"}}'
        )
        logger.info(f"Session started for {agent_name}")

        await session.generate_reply(
            instructions=f"Greet the other party as {agent_name.replace('-agent', '')}."
        )
    except Exception as e:
        logger.error(f"Error in negotiation session: {e}", exc_info=True)
        await ctx.shutdown()

# -----------------------
# Start worker
# -----------------------
if __name__ == "__main__":
    cli.run_app(server)
