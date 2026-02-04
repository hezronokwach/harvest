from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json
from dotenv import load_dotenv
from pathlib import Path
from livekit import api
from livekit.api import CreateAgentDispatchRequest, ListRoomsRequest, SendDataRequest
from contextlib import asynccontextmanager

load_dotenv()

# Global variable for LiveKit API client
lk_api = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global lk_api
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL") or os.getenv("NEXT_PUBLIC_LIVEKIT_URL")

    # Ensure URL is HTTP(S) for API calls, not WSS
    if lk_url and lk_url.startswith("wss://"):
        lk_url = lk_url.replace("wss://", "https://")

    if api_key and api_secret and lk_url:
        print(f"📡 Initializing LiveKit API client for {lk_url}")
        lk_api = api.LiveKitAPI(lk_url, api_key, api_secret)
    else:
        print("⚠️ LiveKit credentials missing! API endpoints will fail.")
        
    yield
    
    if lk_api:
        await lk_api.aclose()
        print("🔌 LiveKit API client closed.")

app = FastAPI(title="Harvest Backend", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Idempotency guard for active calls
active_calls = set()

@app.get("/")
async def root():
    return {"message": "Harvest Backend API is running"}

@app.get("/hume/token")
async def get_hume_token():
    """
    Fetches a Hume access token using the API key and Secret.
    """
    import requests
    from requests.auth import HTTPBasicAuth
    
    api_key = os.getenv("HUME_API_KEY")
    api_secret = os.getenv("HUME_API_SECRET")

    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="Hume credentials not configured")

    try:
        res = requests.post(
            "https://api.hume.ai/oauth2-cc/token",
            auth=HTTPBasicAuth(api_key, api_secret),
            data={"grant_type": "client_credentials"},
            timeout=10
        )
        res.raise_for_status()
        data = res.json()
        return {"accessToken": data["access_token"]}
    except Exception as e:
        print(f"Hume Token Fetch Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch Hume access token: {str(e)}")

@app.get("/livekit/token")
async def get_livekit_token(participant_name: str, persona: str = "Halima", room_name: str = None):
    """
    Generates a LiveKit access token for a participant to join a room.
    If room_name is not provided, joins their presence room.
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    # The presence room is unique to this persona if no specific room provided
    if not room_name:
        room_name = f"presence-{persona.lower()}"

    # Presence rooms are UI-only, no agents dispatched here
    token = api.AccessToken(api_key, api_secret) \
        .with_identity(f"user-{persona.lower()}") \
        .with_name(participant_name) \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
        ))

    return {"token": token.to_jwt(), "room": room_name}

@app.post("/negotiation/call")
async def start_call(room_name: str):
    """
    Initiates a negotiation call by dispatching both agents into a shared call room.
    Idempotent - prevents duplicate dispatch on repeated clicks.
    """
    # Idempotency check
    if room_name in active_calls:
        return {
            "status": "already_running",
            "room": room_name,
            "message": "Call already in progress"
        }
    
    active_calls.add(room_name)
    
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not api_key or not api_secret or not lk_url:
        active_calls.discard(room_name)
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
    
    try:
        # Dispatch Halima into call room
        await lk_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="negotiation-worker",
                metadata='{"role": "seller", "persona": "Halima"}',
            )
        )

        # Dispatch Alex into call room
        await lk_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="negotiation-worker",
                metadata='{"role": "buyer", "persona": "Alex"}',
            )
        )

        return {
            "status": "call_started",
            "room": room_name,
            "agents": ["Halima", "Alex"]
        }

    except Exception as e:
        active_calls.discard(room_name)  # Remove from active calls on error
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/negotiation/end")
async def end_call(room_name: str):
    """
    Ends a negotiation call and removes it from active calls.
    This allows the room to be reused for future calls.
    """
    active_calls.discard(room_name)
    return {
        "status": "call_ended",
        "room": room_name
    }

# -------------------------------------------------
# Helpers
# -------------------------------------------------
async def is_persona_online(persona: str) -> bool:
    """Helper to check if a persona is online in their presence room"""
    if not lk_api:
        return False
        
    presence_room = f"presence-{persona.lower()}"
    try:
        # Canonical way in Python SDK: Pass ListRoomsRequest and access .rooms
        rooms_resp = await lk_api.room.list_rooms(ListRoomsRequest())
        rooms = rooms_resp.rooms
        
        # Find room and check for participants
        target = next((r for r in rooms if r.name == presence_room), None)
        return bool(target and target.num_participants > 0)
    except Exception as e:
        print(f"🕵️ Error checking presence for {persona}: {e}")
        return False

# -------------------------------------------------
# Call Signaling Models
# -------------------------------------------------
class CallOfferRequest(BaseModel):
    from_persona: str
    to_persona: str

class CallAcceptRequest(BaseModel):
    from_persona: str
    to_persona: str
    meeting_id: str

class CallDeclineRequest(BaseModel):
    from_persona: str
    to_persona: str

# -------------------------------------------------
# Call Signaling Endpoints
# -------------------------------------------------
@app.post("/call/offer")
async def call_offer(request: CallOfferRequest):
    """Send call offer to target persona's presence room via data message"""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not lk_api:
        raise HTTPException(status_code=500, detail="LiveKit API not initialized")
    
    print(f"📦 [OFFER] From: {request.from_persona}, To: {request.to_persona}")
    to_room = f"presence-{request.to_persona.lower()}"
    
    try:
        # Checking presence
        print(f"🕵️  [OFFER] Checking presence for {request.to_persona}...")
        is_online = await is_persona_online(request.to_persona)
        
        if not is_online:
            print(f"🛑 [OFFER] Target {request.to_persona} is OFFLINE.")
            return {"status": "offline", "to": request.to_persona}

        print(f"📡 [OFFER] Sending CALL_OFFER to {to_room}...")
        # Send data message using RoomService
        await lk_api.room.send_data(
            SendDataRequest(
                room=to_room,
                data=json.dumps({
                    "type": "CALL_OFFER",
                    "from": request.from_persona
                }).encode('utf-8'),
                kind=api.DataPacket.Kind.RELIABLE
            )
        )
        print(f"✔️  [OFFER] Successfully sent OFFER signal.")
        return {"status": "offer_sent", "to": request.to_persona}
    except Exception as e:
        print(f"❌ Call Offer Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/call/accept")
async def call_accept(request: CallAcceptRequest):
    """Accept call: create call room, dispatch agents, notify caller"""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not lk_api:
        raise HTTPException(status_code=500, detail="LiveKit API not initialized")
    
    print(f"📦 [ACCEPT] From: {request.from_persona}, To: {request.to_persona}, MeetingID: {request.meeting_id}")
    call_room = f"call-{request.meeting_id.lower().replace(' ', '_')}"
    
    if call_room in active_calls:
        print(f"⚠️ [ACCEPT] Call room {call_room} already active, skipping dispatch.")
        return {"status": "already_running", "room": call_room}
    
    active_calls.add(call_room)
    
    try:
        print(f"🤖 [ACCEPT] Dispatching agents to {call_room}...")
        # Dispatch both agents
        await lk_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=call_room,
                agent_name="negotiation-worker",
                metadata='{"role": "seller", "persona": "Halima"}',
            )
        )
        await lk_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=call_room,
                agent_name="negotiation-worker",
                metadata='{"role": "buyer", "persona": "Alex"}',
            )
        )
        
        # Notify caller
        caller_room = f"presence-{request.from_persona.lower()}"
        print(f"📡 [ACCEPT] Notifying caller {request.from_persona} in {caller_room}...")
        await lk_api.room.send_data(
            SendDataRequest(
                room=caller_room,
                data=json.dumps({
                    "type": "CALL_ACCEPTED",
                    "by": request.to_persona,
                    "room": call_room
                }).encode('utf-8'),
                kind=api.DataPacket.Kind.RELIABLE
            )
        )
        
        print(f"✔️  [ACCEPT] Flow complete for room {call_room}")
        return {"status": "accepted", "room": call_room, "agents": ["Halima", "Alex"]}
    except Exception as e:
        active_calls.discard(call_room)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/call/decline")
async def call_decline(request: CallDeclineRequest):
    """Decline incoming call and notify caller"""
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not lk_api:
        raise HTTPException(status_code=500, detail="LiveKit API not initialized")
    
    caller_room = f"presence-{request.from_persona.lower()}"
    
    try:
        await lk_api.room.send_data(
            SendDataRequest(
                room=caller_room,
                data=json.dumps({
                    "type": "CALL_DECLINED",
                    "by": request.to_persona
                }).encode('utf-8'),
                kind=api.DataPacket.Kind.RELIABLE
            )
        )
        return {"status": "declined"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market-price/{crop}")
async def get_market_price(crop: str):
    prices = {"maize": 1.25, "beans": 0.85}
    price = prices.get(crop.lower(), 1.0)
    return {"crop": crop, "price": price, "unit": "kg"}

@app.get("/persona/status/{persona}")
async def get_persona_status(persona: str):
    """Check if a persona is online in their presence room"""
    is_online = await is_persona_online(persona)
    return {"status": "online" if is_online else "offline"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
