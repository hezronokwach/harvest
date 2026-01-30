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

app = FastAPI(title="EchoYield Backend")

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
    return {"message": "EchoYield Backend API is running"}

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
async def get_livekit_token(participant_name: str, room_name: str = "BARN_ROOM_01"):
    """
    Generates a LiveKit access token for a participant.
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    token = api.AccessToken(api_key, api_secret) \
        .with_identity(participant_name) \
        .with_name(participant_name) \
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
        ))

    return {"token": token.to_jwt()}

@app.post("/livekit/dispatch")
async def dispatch_agents(room_name: str = "BARN_ROOM_01"):
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")

    if not api_key or not api_secret or not lk_url:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    client = api.LiveKitAPI(lk_url, api_key, api_secret)

    try:
        # Dispatch Juma
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="juma-agent",
                metadata='{"role": "seller", "persona": "Juma"}',
            )
        )

        # Dispatch Alex
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room_name,
                agent_name="alex-agent",
                metadata='{"role": "buyer", "persona": "Alex"}',
            )
        )

        await client.aclose()
        return {"status": "dispatched", "agents": ["juma-agent", "alex-agent"]}

    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/market-price/{crop}")
async def get_market_price(crop: str):
    """
    Standard market price lookup.
    Used by Hume Agent Tools to stay grounded in real data.
    """
    prices = {
        "potato": 1.25,
        "maize": 0.85,
        "tomatoes": 2.10
    }
    price = prices.get(crop.lower())
    if price is None:
        raise HTTPException(status_code=404, detail="Crop price not found")
    
    return {
        "crop": crop, 
        "price": price, 
        "unit": "kg", 
        "currency": "USD",
        "market_trend": "Rising" if price > 1.0 else "Stable"
    }

@app.post("/negotiation/strategy")
async def get_strategy_hint(buyer_stress: float, buyer_urgency: float):
    """
    Tactical Empathy Orchestrator.
    Determines if Juma should hold firm, flinch, or use silence.
    """
    hint = "[ Alex sounds controlled. Hold your price. ]"
    
    if buyer_stress > 0.7:
        hint = "[ Detect high stress in Buyer. Use Mirroring and Tactical Silence. ]"
    elif buyer_urgency > 0.8:
        hint = "[ Buyer is hurried. Hold the anchor at $1.25. They need to move. ]"
        
    return {"hint": hint}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
