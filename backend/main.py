from fastapi import FastAPI, HTTPException
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="EchoYield Backend")

@app.get("/")
async def root():
    return {"message": "EchoYield Backend API is running"}

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
