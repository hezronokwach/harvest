from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
from pathlib import Path
from livekit import api
from livekit.api import CreateAgentDispatchRequest


if not load_dotenv():
    # If that fails or file missing, try parent dir
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)

if not os.getenv("LIVEKIT_URL") and os.getenv("NEXT_PUBLIC_LIVEKIT_URL"):
    os.environ["LIVEKIT_URL"] = os.getenv("NEXT_PUBLIC_LIVEKIT_URL")

app = FastAPI(title="Harvest Backend")

# Add CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    token = api.AccessToken(api_key, api_secret) \
        .with_identity(f"user-{persona.lower()}") \
        .with_name(participant_name) \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
        )) \
        .with_room_config(api.RoomConfiguration(
            agents=[
                api.RoomAgentDispatch(
                    agent_name="negotiation-worker",
                    metadata=f'{{"role": "{"seller" if persona == "Halima" else "buyer"}", "persona": "{persona}"}}'
                )
            ]
        ))

    return {"token": token.to_jwt(), "room": room_name}

@app.post("/negotiation/call")
async def start_call(room_name: str):
    """
    Bridges Halima and Alex into a specific shared call room.
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")

    if not api_key or not api_secret or not lk_url:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    client = api.LiveKitAPI(lk_url, api_key, api_secret)
    
    try:
        # Check if agents are already in the room (optional but good for multi-user)
        # For simplicity in demo, we just dispatch. LiveKit handles dispatch requests.
        
        # Dispatch Halima into call room
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="negotiation-worker",
                metadata='{"role": "seller", "persona": "Halima"}',
            )
        )

        # Dispatch Alex into call room
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="negotiation-worker",
                metadata='{"role": "buyer", "persona": "Alex"}',
            )
        )

        await client.aclose()
        return {
            "status": "call_started",
            "room": room_name,
            "agents": ["Halima", "Alex"]
        }

    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market-price/{crop}")
async def get_market_price(crop: str):
    prices = {"maize": 1.25, "beans": 0.85}
    price = prices.get(crop.lower(), 1.0)
    return {"crop": crop, "price": price, "unit": "kg"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

