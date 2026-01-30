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
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit import rtc

load_dotenv()

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
    proc.userdata["vad"] = silero.VAD.load()

server.setup_fnc = prewarm

# -----------------------
# Juma agent
# -----------------------
@server.rtc_session(agent_name="juma-agent")
async def juma_entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=hume.TTS(
            voice=hume.VoiceByName(
                name="Male English Actor",
                provider=hume.VoiceProvider.hume,
            ),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=NegotiationAgent(
            instructions=(
                "You are Juma, a protective and firm maize farmer. "
                "Your goal is to sell your harvest for at least $1.15/kg. "
                "You speak with a warm but steady tone. "
                "Defend your price using tactical empathy. Speak concisely."
            )
        ),
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

    await session.generate_reply(
        instructions="Join the negotiation and greet Alex confidently."
    )

# -----------------------
# Alex agent
# -----------------------
@server.rtc_session(agent_name="alex-agent")
async def alex_entrypoint(ctx: JobContext):
    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=hume.TTS(
            voice=hume.VoiceByName(
                name="Kora",
                provider=hume.VoiceProvider.hume,
            ),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=NegotiationAgent(
            instructions=(
                "You are Alex, a skeptical and hurried commodity buyer. "
                "Your goal is to buy maize for as low as possible, ideally $0.90/kg. "
                "You sound impatient and try to anchor the price low. Speak concisely."
            )
        ),
        room=ctx.room,
    )

    await session.generate_reply(
        instructions="Open negotiations aggressively."
    )

# -----------------------
# Start worker
# -----------------------
if __name__ == "__main__":
    cli.run_app(server)
