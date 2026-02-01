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
from livekit.agents import inference
from livekit.plugins import silero, noise_cancellation, deepgram, groq, hume, openai
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
    "turns": 0,
    "max_rounds": 8,
    "sessions": {},
    "shutting_down": False,
}

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
        price: Annotated[float, Field(description="Proposed price per kilogram in USD")],
    ) -> None:
        """Tool for agents to propose a price during negotiation"""
        agent_label = "Halima" if "juma" in self.agent_name.lower() else "Alex"
        logger.info(f"💰 [PRICE TOOL CALLED] {agent_label}: ${price:.2f}")

        try:
            await self.room_participant.publish_data(
                json.dumps({
                    "type": "price_update",
                    "agent": agent_label,
                    "price": round(price, 2),
                }).encode()
            )
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
        meta = json.loads(ctx.job.metadata)
        agent_name = "juma-agent" if meta["persona"] == "Juma" else "alex-agent"

    logger.info(f"Starting {agent_name}")

    if agent_name == "juma-agent":
        instructions = "You are Halima, a Kenyan farmer. Negotiate $1.25/kg. Be warm and firm."
    else:
        instructions = "You are Alex, a commodity buyer. Target $0.90-$1.05/kg. Be professional."

    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=hume.TTS(
            voice=hume.VoiceByName(
                name="Kora" if agent_name == "juma-agent" else "Big Dicky",
                provider="HUME_AI",
            ),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
        turn_detection=None,  # ✅ Manual turn control
    )

    await session.start(
        agent=NegotiationAgent(
            instructions=instructions,
            agent_name=agent_name,
            room_participant=ctx.room.local_participant
        ),
        room=ctx.room,
    )

    STATE["sessions"][agent_name] = session
    logger.info(f"Session ready: {agent_name}")

    # -------------------------------------------------
    # ORCHESTRATION BRIDGE
    # -------------------------------------------------
    
    async def publish_timeline():
        logger.info(f"📊 TIMELINE → round={STATE['rounds']} turn={STATE['turns']}/{STATE['max_rounds']}")
        payload = json.dumps({
            "type": "negotiation_timeline",
            "turn": STATE["turns"],
            "round": STATE["rounds"],
            "max_rounds": STATE["max_rounds"]
        }).encode()
        await ctx.room.local_participant.publish_data(payload)

    # Data Sync Handler (Actor side + Alex Ack listener)
    def on_data_received(payload: rtc.DataPacket, participant=None):
        try:
            data = json.loads(payload.data.decode())
            
            # 1. Halima Logic: Receive turn -> Speak -> Signal Done
            if data.get("type") == "HALIMA_TURN" and agent_name == "juma-agent":
                if STATE.get("shutting_down"):
                    logger.warning("⛔ Skipping Halima reply: shutting down")
                    return
                
                logger.info("✅ Halima received HALIMA_TURN trigger")

                async def halima_reply_and_ack():
                    # ✅ Await ensures she finishes queuing before the next signal
                    await session.generate_reply(instructions=data["instructions"], allow_interruptions=False)
                    # Signal completion to Alex (Master)
                    await ctx.room.local_participant.publish_data(json.dumps({
                        "type": "HALIMA_DONE"
                    }).encode())
                    logger.info("📤 Halima sent HALIMA_DONE signal")

                asyncio.create_task(halima_reply_and_ack())

            # 2. Alex Logic: Receive Ack -> Unblock loop
            if data.get("type") == "HALIMA_DONE" and agent_name == "alex-agent":
                logger.info("📩 Alex received HALIMA_DONE ack")
                if "halima_done_future" in STATE and not STATE["halima_done_future"].done():
                    STATE["halima_done_future"].set_result(True)

        except Exception as e:
            logger.error(f"❌ Data handler error: {e}")

    # Register room events
    ctx.room.on("data_received", on_data_received)

    # -------------------------------------------------
    # THE NEGOTIATION LOOP (Master Logic)
    # -------------------------------------------------
    async def run_negotiation():
        logger.info("🎮 Negotiation loop started")
        
        # Wait for both agents to be in the room
        while len(ctx.room.remote_participants) < 1:
            await asyncio.sleep(1)
            logger.info("⏳ Alex waiting for Halima...")

        await publish_timeline() # 0/0
        
        while STATE["rounds"] < STATE["max_rounds"] and not STATE.get("shutting_down"):
            logger.info(f"🏗️ ROUND {STATE['rounds'] + 1}")
            
            # Setup ack future
            STATE["halima_done_future"] = asyncio.get_event_loop().create_future()

            # 1. Trigger Halima to speak
            instr = "Greet Alex and state your price ($1.25)." if STATE["rounds"] == 0 else "Respond respectfully to Alex."
            await ctx.room.local_participant.publish_data(json.dumps({
                "type": "HALIMA_TURN",
                "instructions": instr
            }).encode())
            logger.info("✅ Halima turn triggered. Alex waiting for HALIMA_DONE...")
            
            # Yield as requested
            await asyncio.sleep(0)

            # Wait for Halima to finish queuing
            try:
                await asyncio.wait_for(STATE["halima_done_future"], timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("⏰ Timeout waiting for HALIMA_DONE - proceeding anyway.")

            if STATE.get("shutting_down"): break

            # 2. Alex speaks 
            logger.info("🎤 Alex starts speaking turn...")
            await session.generate_reply(instructions="Respond respectfully to Halima.", allow_interruptions=False)
            
            # 3. Advance state logically
            STATE["rounds"] += 1
            STATE["turns"] = STATE["rounds"] * 2
            
            logger.info(f"🔄 ROUND {STATE['rounds']} completed. TURN {STATE['turns']}")
            await publish_timeline()
            
        STATE["shutting_down"] = True
        logger.info("🛑 Negotiation complete. Disconnecting...")
        await ctx.room.disconnect()

    # Start the Master Loop ONLY if we are Alex
    if agent_name == "alex-agent":
        asyncio.create_task(run_negotiation())

    # Keep alive
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
            load_threshold=1.5,
        )
    )