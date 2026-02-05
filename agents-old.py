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
    def __init__(self, instructions: str, agent_name: str):
        super().__init__(instructions=instructions)
        self.agent_name = agent_name
        self.room_participant = None  # Assigned after session.start()

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

        # Track actual concessions (changed fields only) BEFORE overwriting STATE
        prev = STATE["offers"].get(agent_label.lower())
        if prev:
            for k in ("price", "delivery_included", "transport_paid_by", "payment_terms"):
                if prev.get(k) != offer.get(k):
                    STATE["concessions"][agent_label.lower()].add(k)

        # Now update STATE
        STATE["offers"][agent_label.lower()] = offer

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
    logger.info("Starting dual-agent negotiation")

    # Define instructions for both agents
    HALIMA_INSTRUCTIONS = """You are Halima, a Kenyan farmer selling bulk maize.
Goal: Maximize total value.
Negotiate using: price per kg (target $1.25), delivery inclusion, transport responsibility, and payment timing.
Strategy: Concede occasionally but not repeatedly on the same dimension. 
Be warm and practical. Explain constraints (fertilizer, labor, cash flow) naturally without repeating the same reason twice.
If you are starting the negotiation, you must make an initial concrete offer.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer.
If the buyer meets your minimum acceptable terms, you should accept the deal.
You are expected to vary price over the negotiation. Repeating the same price more than twice without justification is not acceptable."""

    ALEX_INSTRUCTIONS = """You are Alex, a professional commodity buyer.
Goal: Minimize total landed cost and risk.
Strategy: Evaluate offers holistically. You may accept higher prices if delivery or payment terms improve.
Be concise and analytical. reject and explain why, or counter with a different bundle.
Only call propose_offer when making a concrete counter-offer. You may speak without making an offer.
If an offer meets your target total cost and risk, you should accept it instead of continuing.
You are expected to vary price over the negotiation. Repeating the same price more than twice without justification is not acceptable."""

    await ctx.connect()

    # Create two separate sessions
    halima_session = AgentSession(
        stt=deepgram.STT(),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=hume.TTS(
            voice=hume.VoiceByName(name="Kora", provider="HUME_AI"),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
    )

    alex_session = AgentSession(
        stt=deepgram.STT(),
        llm=inference.LLM(model="openai/gpt-4o-mini"),
        tts=hume.TTS(
            voice=hume.VoiceByName(name="Big Dicky", provider="HUME_AI"),
            instant_mode=True,
        ),
        vad=ctx.proc.userdata["vad"],
    )

    # Create both agents (room_participant assigned after session start)
    halima_agent = NegotiationAgent(
        instructions=HALIMA_INSTRUCTIONS,
        agent_name="juma-agent"
    )
    halima_agent.room_participant = ctx.room.local_participant

    alex_agent = NegotiationAgent(
        instructions=ALEX_INSTRUCTIONS,
        agent_name="alex-agent"
    )
    alex_agent.room_participant = ctx.room.local_participant

    # Start both sessions in the same room
    await halima_session.start(
        agent=halima_agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(close_on_disconnect=False),
    )

    await alex_session.start(
        agent=alex_agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(close_on_disconnect=False),
    )

    # Store sessions in STATE
    STATE["sessions"]["halima"] = {"session": halima_session, "agent": halima_agent}
    STATE["sessions"]["alex"] = {"session": alex_session, "agent": alex_agent}
    STATE["accepted_offer"] = None
    
    logger.info("Both sessions started with correct participant attribution")

    # -------------------------------------------------
    # ORCHESTRATION HELPERS
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


    # -------------------------------------------------
    # THE NEGOTIATION LOOP (Master Logic)
    # -------------------------------------------------
    def acceptable_price(round_num, role):
        """Progressive price bands that narrow over rounds"""
        if role == "seller":  # Halima
            return 1.35 - (round_num * 0.02)  # slowly concedes
        else:  # Alex
            return 1.10 + (round_num * 0.03)  # slowly increases

    async def run_negotiation():
        logger.info("🎮 Negotiation loop started")
        
        # Wait for room connection
        while len(ctx.room.remote_participants) < 1:
            await asyncio.sleep(0.5)
            logger.info("⏳ Waiting for all participants...")

        await publish_timeline()  # 0/0
        
        while STATE["rounds"] < STATE["max_rounds"] and not STATE.get("shutting_down"):
            # ✅ GUARD: Mid-round acceptance check
            if STATE.get("accepted_offer"):
                break

            logger.info(f"🏗️ ROUND {STATE['rounds'] + 1}")

            # ======================
            # HALIMA'S TURN
            # ======================
            
            # Build Halima's context from structured state
            last_alex_offer = STATE["offers"]["alex"]
            last_alex_summary = (
                f"Alex's last offer was {last_alex_offer}"
                if last_alex_offer
                else "Alex has not made an offer yet."
            )

            halima_instr = f"""
            Alex status:
            {last_alex_summary}

            Current offers:
            Halima: {STATE['offers']['halima']}
            Alex: {STATE['offers']['alex']}

            You are in round {STATE['rounds'] + 1} of {STATE['max_rounds']}.
            As rounds progress, push toward closure.
            If this is one of the final 2 rounds, prioritize either reaching agreement or clearly walking away.
            """

            # Force price evolution if stale
            last_halima_offer = STATE["offers"]["halima"]
            if (
                last_halima_offer and
                STATE["rounds"] - last_halima_offer["round"] >= 2
            ):
                halima_instr += """
                You have not changed your price recently.
                You MUST adjust the price in this turn.
                """

            if STATE["rounds"] == STATE["max_rounds"] - 1:
                halima_instr += """
                This is the final round.
                You must either accept, make a final offer, or clearly walk away.
                Do not hedge or prolong the negotiation.
                """

            if STATE["rounds"] == 0:
                halima_instr += """
                You are starting the negotiation.
                You MUST make an initial concrete offer now.
                You MUST call propose_offer exactly once in this turn.
                Do NOT describe prices, delivery, or payment terms unless you call the tool.
                """
            else:
                halima_instr += """
                Respond naturally.
                Only call propose_offer if you are making a concrete counter-offer.
                """

            logger.info("🎤 Halima speaking...")
            h = await halima_session.generate_reply(
                instructions=halima_instr,
                allow_interruptions=False,
            )
            await h  # ✅ Halima finished speaking + tools

            # ✅ ALEX ACCEPTANCE GUARD
            halima_offer = STATE["offers"]["halima"]
            if halima_offer:
                # Early acceptance guard: no deals before meaningful exchange
                if STATE["rounds"] < 2:
                    logger.info("⏳ Alex: Too early to accept, continuing negotiation...")
                else:
                    price = halima_offer["price"]
                    delivery = halima_offer["delivery_included"]
                    payment = halima_offer["payment_terms"]

                    if (
                        halima_offer["round"] == STATE["rounds"] and
                        price <= acceptable_price(STATE["rounds"], "buyer") and
                        delivery and payment in ("7_days", "14_days")
                    ):
                        logger.info("✅ Alex accepts Halima's offer")
                        # Let agent formulate acceptance naturally
                        await alex_session.generate_reply(
                            instructions="You agree with these terms. Accept the offer clearly and politely.",
                            allow_interruptions=False,
                        )
                        STATE["accepted_offer"] = halima_offer
                        await publish_negotiation_complete()
                        break

            # ======================
            # ALEX'S TURN
            # ======================

            if STATE.get("shutting_down") or STATE.get("accepted_offer"):
                break

            logger.info("🎤 Alex speaking...")
            
            alex_instr = f"""
            Halima just proposed this offer:
            {STATE['offers']['halima']}
            
            My last offer:
            {STATE['offers']['alex']}

            Speak naturally to Halima. Do not narrate your actions.
            If accepting, say "That sounds good" and confirm terms.
            If countering, just say the new terms.
            """

            # Force price evolution if stale
            last_alex_offer = STATE["offers"]["alex"]
            if (
                last_alex_offer and
                STATE["rounds"] - last_alex_offer["round"] >= 2
            ):
                alex_instr += """
                You have not changed your price recently.
                You MUST adjust the price in this turn.
                """

            a = await alex_session.generate_reply(
                instructions=alex_instr,
                allow_interruptions=False,
            )
            await a  # ✅ Alex finished speaking + tools

            # ✅ HAL IMA ACCEPTANCE GUARD (Halima accepts Alex's offer)
            alex_offer = STATE["offers"]["alex"]

            if alex_offer:
                # Early acceptance guard: no deals before meaningful exchange
                if STATE["rounds"] < 2:
                    logger.info("⏳ Halima: Too early to accept, continuing negotiation...")
                else:
                    price = alex_offer["price"]
                    payment = alex_offer["payment_terms"]
                    concessions_count = len(STATE["concessions"]["alex"])

                    # Stricter thresholds: Force price >= dynamic threshold AND multiple concessions
                    if (
                        alex_offer["round"] == STATE["rounds"] and
                        price >= acceptable_price(STATE["rounds"], "seller") and
                        payment in ("7_days", "14_days") and
                        concessions_count > 1
                    ):
                        logger.info("✅ Halima accepts Alex's offer")
                        # Let agent formulate acceptance naturally
                        await halima_session.generate_reply(
                            instructions="You agree with these terms. Accept the offer clearly and politely.",
                            allow_interruptions=False,
                        )
                        STATE["accepted_offer"] = alex_offer
                        await publish_negotiation_complete()
                        break
            
            # 3. Advance state logically (each loop = 2 turns: Halima + Alex)
            STATE["rounds"] += 1
            STATE["turns"] += 2
            
            logger.info(f"🔄 ROUND {STATE['rounds']} completed. TURN {STATE['turns']}")
            await publish_timeline()

        # No deal closure message
        if not STATE["accepted_offer"]:
            await halima_session.generate_reply(
                instructions="It looks like we couldn't reach an agreement this time. Thank you for the discussion.",
                allow_interruptions=False,
            )

        logger.info(f"✅ FINAL DEAL: {STATE['accepted_offer']}")
        await publish_negotiation_complete()
        logger.info("🏁 Negotiation loop finished")

    # ✅ Only Alex orchestrates
    meta = json.loads(ctx.job.metadata or "{}")
    if meta.get("persona") == "Alex":
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