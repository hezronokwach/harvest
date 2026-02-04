# Pydantic models for call signaling
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

# Call signaling endpoints
@app.post("/call/offer")
async def call_offer(request: CallOfferRequest):
    """
    Send call offer to target persona's presence room via data message
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not api_key or not api_secret or not lk_url:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
    
    # Target's presence room
    to_room = f"presence-{request.to_persona.lower()}"
    
    client = api.LiveKitAPI(lk_url, api_key, api_secret)
    
    try:
        # Send data message to target's presence room
        await client.room.send_data(
            SendDataRequest(
                room=to_room,
                data=json.dumps({
                    "type": "CALL_OFFER",
                    "from": request.from_persona
                }).encode('utf-8'),
                kind=api.DataPacket_Kind.RELIABLE
            )
        )
        
        await client.aclose()
        return {"status": "offer_sent", "to": request.to_persona}
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/call/accept")
async def call_accept(request: CallAcceptRequest):
    """
    Accept call: create call room, dispatch agents, notify caller
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not api_key or not api_secret or not lk_url:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
    
    call_room = f"call-{request.meeting_id.lower().replace(' ', '_')}"
    
    # Check idempotency
    if call_room in active_calls:
        return {
            "status": "already_running",
            "room": call_room
        }
    
    active_calls.add(call_room)
    
    client = api.LiveKitAPI(lk_url, api_key, api_secret)
    
    try:
        # Dispatch both agents
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=call_room,
                agent_name="negotiation-worker",
                metadata='{"role": "seller", "persona": "Halima"}',
            )
        )
        
        await client.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=call_room,
                agent_name="negotiation-worker",
                metadata='{"role": "buyer", "persona": "Alex"}',
            )
        )
        
        # Notify caller that call was accepted
        caller_room = f"presence-{request.from_persona.lower()}"
        await client.room.send_data(
            SendDataRequest(
                room=caller_room,
                data=json.dumps({
                    "type": "CALL_ACCEPTED",
                    "by": request.to_persona,
                    "room": call_room
                }).encode('utf-8'),
                kind=api.DataPacket_Kind.RELIABLE
            )
        )
        
        await client.aclose()
        return {
            "status": "accepted",
            "room": call_room,
            "agents": ["Halima", "Alex"]
        }
    except Exception as e:
        active_calls.discard(call_room)
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/call/decline")
async def call_decline(request: CallDeclineRequest):
    """
    Decline incoming call and notify caller
    """
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    lk_url = os.getenv("LIVEKIT_URL")
    
    if not api_key or not api_secret or not lk_url:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
    
    caller_room = f"presence-{request.from_persona.lower()}"
    
    client = api.LiveKitAPI(lk_url, api_key, api_secret)
    
    try:
        # Notify caller that call was declined
        await client.room.send_data(
            SendDataRequest(
                room=caller_room,
                data=json.dumps({
                    "type": "CALL_DECLINED",
                    "by": request.to_persona
                }).encode('utf-8'),
                kind=api.DataPacket_Kind.RELIABLE
            )
        )
        
        await client.aclose()
        return {"status": "declined"}
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=500, detail=str(e))
