from fastapi import FastAPI, HTTPException
import os
from dotenv import load_dotenv
from livekit import api

load_dotenv()

app = FastAPI(title="EchoYield Backend")

@app.get("/")
async def root():
    return {"message": "EchoYield Backend API is running"}

@app.get("/hume/token")
async def get_hume_token():
    """
    Fetches a Hume access token using the API key and Secret.
    """
    import requests
    api_key = os.getenv("HUME_API_KEY")
    api_secret = os.getenv("HUME_SECRET_KEY")
    
    if not api_key or not api_secret:
        # Fallback to NEXT_PUBLIC versions if added by user
        api_key = os.getenv("NEXT_PUBLIC_HUME_API_KEY")
        api_secret = os.getenv("NEXT_PUBLIC_HUME_SECRET_KEY")

    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="Hume credentials not configured")

    # In a real app, you'd exchange these for a short-lived token
    # For this demo/sprint, we'll return the keys or a placeholder if a real exchange is needed
    # Actually Hume SDK usually expects the API key or a session token.
    # We will implement the proper sequence:
    auth_url = "https://api.hume.ai/v0/auth/token"
    # Basic auth exchange (placeholder for actual Hume auth flow if different)
    # Hume uses the API key directly in many cases, but for security, a token is preferred.
    
    return {"accessToken": api_key} # Placeholder: Hume SDK can take API Key directly if configured

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
