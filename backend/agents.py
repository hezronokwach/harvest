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
# Shared Turn State (for agent-to-agent orchestration)
# -----------------------
TURN_STATE = {
    "current": "juma",  # Who has the turn
    "rounds": 0,        # How many exchanges have happened
    "max_rounds": 8,    # Stop after this many turns
    "sessions": {},     # Maps agent_name -> AgentSession
}

# -----------------------
# Unified Entrypoint
# -----------------------
@server.rtc_session()
async def negotiation_entrypoint(ctx: JobContext):
    agent_name = ctx.job.agent_name
    logger.info(f"Incoming dispatch request. Agent Name: '{agent_name}' in room: {ctx.room.name}")

    # If it's the generic worker name, or empty, check metadata
    if agent_name == "negotiation-worker" or not agent_name:
        if ctx.job.metadata:
            import json
            try:
                meta = json.loads(ctx.job.metadata)
                if meta.get("persona") == "Juma":
                    agent_name = "juma-agent"
                elif meta.get("persona") == "Alex":
                    agent_name = "alex-agent"
                logger.info(f"Resolved agent name from metadata: {agent_name}")
            except Exception as e:
                logger.warning(f"Failed to parse metadata: {e}")
        else:
             logger.warning("No metadata provided for generic dispatch")

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
                ),
                # CRITICAL: Allow agents to listen to other agents (for bot-to-bot negotiation)
                participant_kinds=[
                    rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
                    rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
                    rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
                    rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR,
                ]
            ),
        )

        # Emit an initial thought
        await ctx.room.local_participant.publish_data(
            f'{{"type": "thought", "text": "{thought_text}"}}'
        )
        logger.info(f"Session started for {agent_name}")

        # Register this session in shared state
        TURN_STATE["sessions"][agent_name] = session

        # Agent-to-Agent Turn Orchestration
        # Use conversation_item_added to get the ACTUAL text spoken
        def on_conversation_item(event):
            """Called when a conversation item (message) is added"""
            import asyncio
            
            # Unwrap the actual conversation item from the event
            item = event.item
            
            # Only react to agent speech (not user input)
            if item.role != "assistant":
                return
            
            # ✅ Only react if it's THIS agent's turn
            # This is the single source of truth for turn ownership
            if TURN_STATE["current"] != agent_name:
                return
            
            # Check if we've hit max rounds
            if TURN_STATE["rounds"] >= TURN_STATE["max_rounds"]:
                logger.info(f"[{agent_name}] Max rounds reached, stopping negotiation")
                return
            
            # Increment round counter
            TURN_STATE["rounds"] += 1
            
            # Determine the other agent
            other_agent = "alex-agent" if agent_name == "juma-agent" else "juma-agent"
            
            # Get the other agent's session
            other_session = TURN_STATE["sessions"].get(other_agent)
            if not other_session:
                logger.warning(f"[{agent_name}] Could not find session for {other_agent}")
                return
            
            logger.info(
                f"[TURN {TURN_STATE['rounds']}] "
                f"{agent_name} spoke. Handing turn to {other_agent}"
            )
            
            # Hand over the turn BEFORE scheduling the reply
            TURN_STATE["current"] = other_agent
            
            # Check if this is the last turn - if so, ask for a closing statement
            if TURN_STATE["rounds"] == TURN_STATE["max_rounds"]:
                async def final_turn():
                    await asyncio.sleep(0.6)
                    other_session.generate_reply(
                        instructions=(
                            f"You are responding to this final statement:\n\n"
                            f'"{item.content}"\n\n'
                            f"Summarize the negotiation outcome in one sentence and say goodbye."
                        ),
                        allow_interruptions=False,
                    )
                asyncio.create_task(final_turn())
                return
            
            # Async handoff with proper delay and context
            async def handoff():
                await asyncio.sleep(0.6)  # Natural pacing
                other_session.generate_reply(
                    instructions=(
                        f"You are responding to this statement:\n\n"
                        f'"{item.content}"\n\n'
                        f"Continue the negotiation naturally."
                    ),
                    allow_interruptions=False,
                )
            
            asyncio.create_task(handoff())

        session.on("conversation_item_added", on_conversation_item)

        # Leader/Follower Logic: Only Juma starts the conversation
        if agent_name == "juma-agent":
            await session.generate_reply(
                instructions="Greet Alex briefly and state your price. Keep it short - one or two sentences max.",
                allow_interruptions=False,
            )
        else:
            # Alex waits for Juma to speak first
            logger.info("Alex is waiting for Juma to speak...")
        
        # Keep the session running by waiting for the user to leave
        # This prevents the entrypoint from exiting and closing the session prematurely
        def p_disconnect(p: rtc.RemoteParticipant):
             logger.info(f"Participant {p.identity} disconnected")

        def on_track_subscribed(track: rtc.Track, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
            logger.info(f"[{agent_name}] Subscribed to track {track.sid} from {participant.identity} ({participant.kind})")

        ctx.room.on("participant_disconnected", p_disconnect)
        ctx.room.on("track_subscribed", on_track_subscribed)
        
        # Wait indefinitely until the job is closed (e.g. user leaves)
        # We can simulate this by waiting on a future or just sleeping loop
        import asyncio
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Error in negotiation session: {e}", exc_info=True)
        ctx.shutdown()

# -----------------------
# Start worker
# -----------------------
from livekit.agents import WorkerOptions

if __name__ == "__main__":
    # Enable more verbose logging for debugging
    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)
    
    # Relax load threshold for local dev (allow up to 95% CPU/Memory before rejecting)
    opts = WorkerOptions(
        entrypoint_fnc=negotiation_entrypoint,
        prewarm_fnc=prewarm,
        agent_name="negotiation-worker",
        load_threshold=1.5,
    )
    cli.run_app(opts)
