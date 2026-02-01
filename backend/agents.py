from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AgentServer,
    JobContext,
    JobProcess,
    cli,
    room_io,
    function_tool,
)
from livekit.plugins import silero, noise_cancellation, deepgram, groq, hume
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit import rtc
from typing import Annotated
from pydantic import Field
import logging
import asyncio
import json
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
# Agent with Tool
# -------------------------------------------------
class NegotiationAgent(Agent):
    def __init__(self, instructions: str, agent_name: str, room_participant):
        super().__init__(instructions=instructions)
        self.agent_name = agent_name
        self.room_participant = room_participant

    @function_tool
    async def propose_price(
        self,
        price: Annotated[
            float,
            Field(description="Proposed price per kilogram in USD")
        ]
    ):
        """Tool for agents to propose a price during negotiation"""
        agent_label = "Halima" if "Halima" in self.agent_name else "Alex"
        logger.info(f"💰 [PRICE TOOL CALLED] {agent_label}: ${price:.2f}")

        try:
            await self.room_participant.publish_data(
                json.dumps({
                    "type": "price_update",
                    "agent": agent_label,
                    "price": round(price, 2),
                }).encode()
            )
            logger.info(f"✅ [PUBLISHED] price_update for {agent_label}: ${price:.2f}")
        except Exception as e:
            logger.error(f"❌ Failed to publish price: {e}")

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
    "GOAL: Reach a fair deal between $1.10 and $1.20 per kilogram while maintaining mutual respect.\n"
    "\n"
    "CRITICAL TOOL USAGE:\n"
    "Whenever you propose or mention a specific price, you MUST immediately call the tool:\n"
    "propose_price(price: float)\n"
    "\n"
    "Speak naturally to Alex, but always use the tool to record your price.\n"
    "Example: Say 'I can offer one dollar twenty per kilogram' and call propose_price(1.20)"
    "CRITICAL RULES:"
    "- NEVER mention tools, functions, APIs, prices as calls, or internal actions."
    "- NEVER say phrases like I am calling, I will now, price value equals, or similar."
    "- Tools are silent internal actions and must not be spoken aloud."
    "- Only speak natural conversational language intended for a human listener."
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
    "GOAL: Reach a deal between $1.00 and $1.15 per kilogram while building a good long-term relationship.\n"
    "\n"
    "CRITICAL TOOL USAGE:\n"
    "Whenever you propose or mention a specific price, you MUST immediately call the tool:\n"
    "propose_price(price: float)\n"
    "\n"
    "Speak naturally to Halima, but always use the tool to record your price.\n"
    "Example: Say 'I can pay one dollar per kilogram' and call propose_price(1.00)"
    "CRITICAL RULES:"
    "- NEVER mention tools, functions, APIs, prices as calls, or internal actions."
    "- NEVER say phrases like I am calling, I will now, price value equals, or similar."
    "- Tools are silent internal actions and must not be spoken aloud."
    "- Only speak natural conversational language intended for a human listener."
)

    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=groq.LLM(model="llama-3.3-70b-versatile"),
        tts=hume.TTS(
            voice=hume.VoiceByName(
                name="Kora" if agent_name == "juma-agent" else "Big Dicky",
                provider="HUME_AI",
            ),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),  # ✅ CRITICAL: Enables speech_finished events
    )

    await session.start(
        agent=NegotiationAgent(
            instructions=instructions,
            agent_name=agent_name,
            room_participant=ctx.room.local_participant
        ),
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
        logger.info(f"🎤 [SPEECH_FINISHED EVENT] Halima speech handler called!")
        STATE["rounds"] += 1
        logger.info(f"🔄 [ROUND {STATE['rounds']}] Halima finished speaking")
        logger.info(f"📝 [SPEECH TEXT] {text}")

        # Notify frontend of round update
        try:
            import json
            payload = json.dumps({
                "type": "round_update",
                "round": STATE["rounds"]
            }).encode('utf-8')
            await ctx.room.local_participant.publish_data(payload)
            logger.info(f"✅ Published round_update: Round {STATE['rounds']}")
        except Exception as e:
            logger.error(f"❌ Failed to publish round update: {e}")

        # Note: Price updates now handled by propose_price() tool call

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
        logger.info(f"🔵 [ALEX SPEECH] {text}")
        
        if negotiation_has_ended(text):
            logger.info("Deal reached! Ending negotiation...")
            await asyncio.sleep(2)
            
            # Close both sessions
            for agent_session in STATE["sessions"].values():
                try:
                    await agent_session.close()
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
            
            await ctx.room.disconnect()
            return

        # Note: Price updates now handled by propose_price() tool call

        await STATE["sessions"]["juma-agent"].generate_reply(
            instructions=f"Respond respectfully to Alex:\n{text}",
            allow_interruptions=False,
        )

    # Attach handlers
    if agent_name == "juma-agent":
        logger.info("🎤 Registering speech_finished handler for Halima (juma-agent)")
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(juma_after_speech(text))
        )
    else:
        logger.info("🎤 Registering speech_finished handler for Alex (alex-agent)")
        session.on(
            "speech_finished",
            lambda text: asyncio.create_task(alex_after_speech(text))
        )

    # -------------------------------------------------
    # START CONVERSATION
    # -------------------------------------------------
    logger.info(f"🚀 Checking if {agent_name} should start conversation...")
    if agent_name == "juma-agent":
        logger.info("✅ Halima (juma-agent) will start the conversation")
        try:
            await session.generate_reply(
                instructions=(
                    "Greet Alex politely and state your starting price, "
                    "while expressing openness to a fair discussion."
                ),
                allow_interruptions=False,
            )
            logger.info("📤 Conversation starter sent to Halima")
        except Exception as e:
            logger.error(f"❌ Failed to generate reply: {e}")
    else:
        logger.info(f"⏸️ {agent_name} waiting for Halima to start")

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