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
    "accepted_offer": None,
    "offers": {
        "halima": None,
        "alex": None,
    },
    "concessions": {
        "halima": set(),
        "alex": set(),
    },
    "halima_offer_future": None,
    "alex_offer_future": None,
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

    # async def transcription_task(self):
    #     """Official hook to capture agent's own speech from the session"""
    #     while self.session is None:
    #         await asyncio.sleep(0.05)
            
    #     async for segment in self.session.transcriptions():
    #         if hasattr(segment, "text"):
    #             self._spoken_buffer.append(segment.text)
    #         else:
    #             self._spoken_buffer.append(str(segment))

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
            "round": STATE.get("rounds", 0),
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
            
            # Resolve the future so the counterpart can proceed
            future_key = "halima_offer_future" if agent_label == "Halima" else "alex_offer_future"
            future = STATE.get(future_key)
            if future and not future.done():
                future.set_result(offer)
                
        except Exception as e:
            logger.error(f"❌ Failed to publish offer: {e}")

    @function_tool
    async def accept_offer(self) -> None:
        agent_label = "Halima" if "juma" in self.agent_name.lower() else "Alex"
        
        # Explicitly resolve the counterpart's offer from state
        offer = STATE["offers"]["alex" if agent_label == "Halima" else "halima"]
        
        if not offer:
            return

        STATE["accepted_offer"] = offer

        await self.room_participant.publish_data(
            json.dumps({
            "type": "OFFER_ACCEPTED",
            "by": agent_label,
            "offer": offer,
        }).encode()
    )

    async def speak_acceptance(self, offer: dict, role: str):
        price = offer["price"]
        delivery = "including delivery" if offer["delivery_included"] else "excluding delivery"
        payment = offer["payment_terms"].replace("_", " ")

        if role == "seller":
            text = (
                f"That works for me. I accept your offer at ${price:.2f} per kilogram, "
                f"{delivery}, with payment in {payment}. "
                "Thank you, I look forward to working together."
            )
        else:
            text = (
                f"Great, I accept your offer at ${price:.2f} per kilogram, "
                f"{delivery}, with payment in {payment}. "
                "We have a deal. Thank you."
            )

        # Force spoken reply
        await self.session.generate_reply(
            instructions=text,
            allow_interruptions=False,
        )

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
Negotiate using: price per kg (target $1.30), delivery inclusion, transport responsibility, and payment timing.
Strategy: Concede occasionally but not repeatedly on the same dimension. 
Be warm and practical. Explain constraints (fertilizer, labor, cash flow) naturally without repeating the same reason twice.
If you are starting the negotiation, you must make an initial concrete offer.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer.
If the buyer meets your minimum acceptable terms, you should accept the deal."""
    else:
        instructions = """You are Alex, a professional commodity buyer.
Goal: Minimize total landed cost and risk.
Strategy: Evaluate offers holistically. You may accept higher prices if delivery or payment terms improve.
Be concise and analytical. reject and explain why, or counter with a different bundle.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer.
If an offer meets your target total cost and risk, you should accept it instead of continuing."""

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
    
    # asyncio.create_task(agent.transcription_task())

    STATE["sessions"][agent_name] = {
        "session": session,
        "agent": agent
    }
    STATE["accepted_offer"] = None
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
                        

                        # ✅ WAIT until speech AND all tool calls (propose_offer) finish
                        await handle

                        # ✅ ONLY NOW unblock Alex
                        await ctx.room.local_participant.publish_data(
                            json.dumps({"type": "HALIMA_DONE"}).encode()
                        )

                        logger.info("📤 Halima sent HALIMA_DONE")
                    finally:
                        STATE["halima_speaking"] = False

                # Dispatch her turn
                asyncio.create_task(halima_reply_and_ack())

            # 2. Alex Logic: Receive Ack -> Store Context -> Unblock loop
            if data.get("type") == "HALIMA_DONE" and agent_name == "alex-agent":
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
        if STATE.get("accepted_offer"):
            return        
        
        # Wait for both agents to be in the room and registered
        while len(ctx.room.remote_participants) < 1 or len(STATE["sessions"]) < 2:
            await asyncio.sleep(1)
            logger.info(f"⏳ Alex waiting... (participants={len(ctx.room.remote_participants)}, agents={len(STATE['sessions'])})")

        await publish_timeline() # 0/0
        
        while STATE["rounds"] < STATE["max_rounds"] and not STATE.get("shutting_down"):
            if STATE.get("accepted_offer"):
                return
            logger.info(f"🏗️ ROUND {STATE['rounds'] + 1}")            
            # Setup ack future
            STATE["halima_done_future"] = asyncio.get_running_loop().create_future()
            STATE["halima_offer_future"] = asyncio.get_running_loop().create_future()

            # 1. Trigger Halima to speak
            last_alex = STATE.get("last_alex_text", "Introductions phase.")
            
            instr = f"""
            Alex last said: "{last_alex}"
            Current offers:
            Halima: {STATE['offers']['halima']}
            Alex: {STATE['offers']['alex']}
            """

            if STATE["rounds"] == 0:
                instr += """
                You are starting the negotiation.
                You MUST make an initial concrete offer now.
                You MUST call propose_offer exactly once in this turn.
                Do NOT describe prices, delivery, or payment terms unless you call the tool.
                """
            else:
                instr += """
                Respond naturally.
                Only call propose_offer if you are making a concrete counter-offer.
                """
            
            await ctx.room.local_participant.publish_data(json.dumps({
                "type": "HALIMA_TURN",
                "instructions": instr
            }).encode())
            logger.info("✅ Halima turn triggered. Alex waiting for HALIMA_DONE...")
            
            # Yield to let Halima start first
            await asyncio.sleep(0.2)

            # Wait for Halima to finish queuing
            try:
                # ✅ Wait for Halima to finish SPEECH
                await asyncio.wait_for(STATE["halima_done_future"], timeout=60.0)

                # ✅ Wait for Halima's OFFER (Logic Sync)
                logger.info("⏳ Alex waiting for Halima's offer...")
                halima_offer = STATE["offers"]["halima"]

                # ✅ ALEX ACCEPTANCE GUARD
                if halima_offer:
                    price = halima_offer["price"]
                    delivery = halima_offer["delivery_included"]
                    payment = halima_offer["payment_terms"]

                    if price <= 1.20 and delivery and payment in ("7_days", "14_days"):
                        logger.info("✅ Alex accepts Halima's offer")
                        
                        STATE["accepted_offer"] = halima_offer

                        await session.generate_reply(
                            instructions=(
                                f"That sounds good. I accept your offer at ${halima_offer['price']:.2f} per kilogram, "
                                f"{'including' if halima_offer['delivery_included'] else 'excluding'} delivery, "
                                f"with payment in {halima_offer['payment_terms'].replace('_', ' ')}. "
                                "We have a deal. Thank you."
                            ),
                            allow_interruptions=False,
                        )

                        await publish_negotiation_complete()
                        return

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
            Halima just proposed this offer:
            {STATE['offers']['halima']}
            
            My last offer:
            {STATE['offers']['alex']}

            Speak naturally to Halima. Do not narrate your actions.
            If accepting, say "That sounds good" and confirm terms.
            If countering, just say the new terms.
            """
            handle = await session.generate_reply(
                instructions=alex_instr,
                allow_interruptions=False,
            )

            # Robust retry loop for transcription lag
            alex_text = ""
            for _ in range(8):
                alex_text = agent.consume_spoken_text()
                if alex_text:
                    break
                await asyncio.sleep(0.05)

            if not alex_text:
                alex_text = ""
                
            STATE["last_alex_text"] = alex_text

            # Broadcast Alex's text so Halima can "hear" it
            await ctx.room.local_participant.publish_data(json.dumps({
                "type": "ALEX_SPEECH",
                "speaker": "Alex",
                "text": alex_text
            }).encode())

            # ✅ HALIMA ACCEPTANCE GUARD (Halima accepts Alex's offer)
            alex_offer = STATE["offers"]["alex"]

            if alex_offer:
                price = alex_offer["price"]
                payment = alex_offer["payment_terms"]
                concessions_count = len(STATE["concessions"]["alex"])

                # Stricter thresholds: Force price >= 1.30 AND multiple concessions
                if price >= 1.30 and payment in ("7_days", "14_days") and concessions_count > 1:
                    logger.info("✅ Halima accepts Alex's offer")
                        
                    STATE["accepted_offer"] = alex_offer

                    await session.generate_reply(
                        instructions=(
                            f"That works for me. I accept your offer at ${alex_offer['price']:.2f} per kilogram, "
                            f"{'including' if alex_offer['delivery_included'] else 'excluding'} delivery, "
                            f"with payment in {alex_offer['payment_terms'].replace('_', ' ')}. "
                            "Thank you, I look forward to working together."
                        ),
                        allow_interruptions=False,
                    )

                    await publish_negotiation_complete()
                    return
            
            # 3. Advance state logically
            STATE["rounds"] += 1
            STATE["turns"] = STATE["rounds"] * 2
            
            logger.info(f"🔄 ROUND {STATE['rounds']} completed. TURN {STATE['turns']}")
            await publish_timeline()
        logger.info(f"✅ FINAL DEAL CLOSED: {STATE['accepted_offer']}")
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