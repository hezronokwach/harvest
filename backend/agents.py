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
    utils,
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
STATE = {
    "rounds": 0,
    "turns": 0,
    "max_rounds": 8,
    "sessions": {},
    "shutting_down": False,
    "halima_speaking": False,
    "offers": {
        "halima": None,
        "alex": None,
    },
    "concessions": {
        "halima": set(),
        "alex": set(),
    },
}

# -------------------------------------------------
# Agent with Tool
# -------------------------------------------------
class NegotiationAgent(Agent):
    def __init__(self, instructions: str, agent_name: str, room_participant):
        super().__init__(instructions=instructions)
        self.agent_name = agent_name
        self.room_participant = room_participant
        self._spoken_buffer = []

    async def transcription_task(self):
        """Official hook to capture agent's own speech from the session"""
        while self.session is None:
            await asyncio.sleep(0.05)
            
        async for segment in self.session.transcriptions():
            if hasattr(segment, "text"):
                self._spoken_buffer.append(segment.text)
            else:
                self._spoken_buffer.append(str(segment))

    def consume_spoken_text(self) -> str:
        """Clear the buffer and return the concatenated speech"""
        text = " ".join(self._spoken_buffer).strip()
        self._spoken_buffer.clear()
        return text

    @function_tool
    async def propose_offer(
        self,
        price: Annotated[float, Field(description="USD per kg")],
        delivery_included: Annotated[bool, Field(description="Whether delivery is included")],
        transport_paid_by: Annotated[str, Field(description="seller or buyer")],
        payment_terms: Annotated[str, Field(description="cash, 7_days, or 14_days")],
    ) -> None:
        """Tool for agents to propose a concrete multi-lever offer"""
        agent_label = "Halima" if "juma" in self.agent_name.lower() else "Alex"
        
        offer = {
            "price": round(price, 2),
            "delivery_included": delivery_included,
            "transport_paid_by": transport_paid_by,
            "payment_terms": payment_terms,
        }

        STATE["offers"][agent_label.lower()] = offer
        STATE["concessions"][agent_label.lower()].update(offer.keys())

        logger.info(f"📦 [OFFER] {agent_label}: {offer}")

        try:
            await self.room_participant.publish_data(
                json.dumps({
                    "type": "offer_update",
                    "agent": agent_label,
                    "offer": offer,
                }).encode()
            )
        except Exception as e:
            logger.error(f"❌ Failed to publish offer: {e}")

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
        instructions = """You are Halima, a Kenyan farmer selling bulk maize.
Goal: Maximize total value.
Negotiate using: price per kg (target $1.25), delivery inclusion, transport responsibility, and payment timing.
Strategy: Concede occasionally but not repeatedly on the same dimension. 
Be warm and practical. Explain constraints (fertilizer, labor, cash flow) naturally without repeating the same reason twice.
If you are starting the negotiation, you must make an initial concrete offer.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer."""
    else:
        instructions = """You are Alex, a professional commodity buyer.
Goal: Minimize total landed cost and risk.
Strategy: Evaluate offers holistically. You may accept higher prices if delivery or payment terms improve.
Be concise and analytical. reject and explain why, or counter with a different bundle.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer."""

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

    agent = NegotiationAgent(
        instructions=instructions,
        agent_name=agent_name,
        room_participant=ctx.room.local_participant
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            close_on_disconnect=False
        ),
    )
    
    asyncio.create_task(agent.transcription_task())

    STATE["sessions"][agent_name] = {
        "session": session,
        "agent": agent
    }
    logger.info(f"Session & Agent ready: {agent_name}")

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

    async def publish_negotiation_complete():
        logger.info("🏁 Sending NEGOTIATION_COMPLETE signal")
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "NEGOTIATION_COMPLETE"}).encode()
        )

    # Data Sync Handler (Actor side + Alex Ack listener)
    def on_data_received(payload: rtc.DataPacket, participant=None):
        try:
            # ⛔ Filter our own broadcasts (prevents duplicate triggers)
            if participant and participant.identity == ctx.room.local_participant.identity:
                return

            data = json.loads(payload.data.decode())
            
            # 1. Halima Logic: Receive turn -> Speak -> Signal Done
            if data.get("type") == "HALIMA_TURN" and agent_name == "juma-agent":
                if STATE.get("shutting_down"):
                    logger.warning("⛔ Skipping Halima reply: shutting down")
                    return
                
                if STATE.get("halima_speaking"):
                    logger.warning("⏳ Halima is already speaking/generating. Ignoring duplicate trigger.")
                    return

                logger.info("✅ Halima received HALIMA_TURN trigger")
                STATE["halima_speaking"] = True

                async def halima_reply_and_ack():
                    try:
                        if STATE.get("shutting_down"): 
                            logger.warning("⛔ Halima task cancelled: shutting down")
                            return

                        # Generate reply and get a SpeechHandle
                        handle = await session.generate_reply(
                            instructions=data["instructions"],
                            allow_interruptions=False,
                        )
                        

                        # Robust retry loop for transcription lag
                        halima_text = ""
                        for _ in range(20):   # up to ~1 second
                            halima_text = agent.consume_spoken_text()
                            if halima_text:
                                break
                            await asyncio.sleep(0.05)

                        if not halima_text:
                            halima_text = "Let me outline an initial offer so we can move forward."

                        # Signal completion and SHARE TEXT to bridge context
                        await ctx.room.local_participant.publish_data(json.dumps({
                            "type": "HALIMA_DONE",
                            "speaker": "Halima",
                            "text": halima_text
                        }).encode())
                        logger.info(f"📤 Halima sent HALIMA_DONE with text: {halima_text}")
                    finally:
                        STATE["halima_speaking"] = False

                # Dispatch her turn
                asyncio.create_task(halima_reply_and_ack())

            # 2. Alex Logic: Receive Ack -> Store Context -> Unblock loop
            if data.get("type") == "HALIMA_DONE" and agent_name == "alex-agent":
                halima_text = data.get("text", "")
                logger.info(f"📩 Alex received Halima text: {halima_text}")
                
                if halima_text:
                    STATE["last_halima_text"] = halima_text

                if "halima_done_future" in STATE and not STATE["halima_done_future"].done():
                    STATE["halima_done_future"].set_result(True)

            # 3. Actor Sync: Halima "hears" Alex
            if data.get("type") == "ALEX_SPEECH" and agent_name == "juma-agent":
                alex_text = data.get("text", "")
                logger.info(f"📥 Halima heard Alex: {alex_text}")
                if alex_text:
                    STATE["last_alex_text"] = alex_text

        except Exception as e:
            logger.error(f"❌ Data handler error: {e}")

    # Register room events
    ctx.room.on("data_received", on_data_received)

    # -------------------------------------------------
    # THE NEGOTIATION LOOP (Master Logic)
    # -------------------------------------------------
    async def run_negotiation():
        logger.info("🎮 Negotiation loop started")
        
        # Wait for both agents to be in the room and registered
        while len(ctx.room.remote_participants) < 1 or len(STATE["sessions"]) < 2:
            await asyncio.sleep(1)
            logger.info(f"⏳ Alex waiting... (participants={len(ctx.room.remote_participants)}, agents={len(STATE['sessions'])})")

        await publish_timeline() # 0/0
        
        while STATE["rounds"] < STATE["max_rounds"] and not STATE.get("shutting_down"):
            logger.info(f"🏗️ ROUND {STATE['rounds'] + 1}")
            
            # Setup ack future
            STATE["halima_done_future"] = asyncio.get_running_loop().create_future()

            # 1. Trigger Halima to speak
            last_alex = STATE.get("last_alex_text", "Introductions phase.")
            
            instr = f"""
            Context:
            Alex last said: "{last_alex}"

            Last offers:
            Halima: {STATE["offers"]["halima"]}
            Alex: {STATE["offers"]["alex"]}

            If no offer has been made yet, make a concrete opening offer.
            Otherwise, continue the negotiation naturally.
            """
            
            await ctx.room.local_participant.publish_data(json.dumps({
                "type": "HALIMA_TURN",
                "instructions": instr
            }).encode())
            logger.info("✅ Halima turn triggered. Alex waiting for HALIMA_DONE...")
            
            # Yield to let Halima start first
            await asyncio.sleep(0.3)

            # Wait for Halima to finish queuing
            try:
                await asyncio.wait_for(STATE["halima_done_future"], timeout=60.0)
            except asyncio.TimeoutError:
                logger.error("❌ Halima did not respond in time (60s timeout). Ending negotiation.")
                STATE["shutting_down"] = True
                break

            if STATE.get("shutting_down"): break

            halima_text = STATE.get("last_halima_text", "Halima is considering your offer.")

            # 2. Alex speaks 
            if STATE.get("shutting_down"): break
            logger.info("🎤 Alex starts speaking turn...")
            
            alex_instr = f"""
            Context:
            Halima last said: "{halima_text}"

            Last offers:
            Halima: {STATE["offers"]["halima"]}
            Alex: {STATE["offers"]["alex"]}

            Continue the negotiation naturally.
            """
            handle = await session.generate_reply(
                instructions=alex_instr,
                allow_interruptions=False,
            )

            # Robust retry loop for transcription lag
            alex_text = ""
            for _ in range(20):
                alex_text = agent.consume_spoken_text()
                if alex_text:
                    break
                await asyncio.sleep(0.05)

            if not alex_text:
                alex_text = "I appreciate your offer. Let me review it."
                
            STATE["last_alex_text"] = alex_text

            # Broadcast Alex's text so Halima can "hear" it
            await ctx.room.local_participant.publish_data(json.dumps({
                "type": "ALEX_SPEECH",
                "speaker": "Alex",
                "text": alex_text
            }).encode())
            
            # 3. Advance state logically
            STATE["rounds"] += 1
            STATE["turns"] = STATE["rounds"] * 2
            
            logger.info(f"🔄 ROUND {STATE['rounds']} completed. TURN {STATE['turns']}")
            await publish_timeline()
            
        await publish_negotiation_complete()
        logger.info("🏁 Negotiation loop finished. Alex entering idle state.")
        return

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