"""Voice conversation endpoints using simplified WebSocket approach."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.services.pipecat_bot_simple import create_simple_bot

logger = structlog.get_logger()

router = APIRouter(prefix="/voice", tags=["voice"])

# Store active connections
_active_connections: dict[str, WebSocket] = {}


class StartCallRequest(BaseModel):
    """Request to start a new voice call."""
    pass


class StartCallResponse(BaseModel):
    """Response with WebSocket URL for joining the call."""
    ws_url: str
    call_id: str
    expires_at: str


@router.post("/start", response_model=StartCallResponse)
async def start_call(request: StartCallRequest):
    """
    Start a new voice call session.
    
    Returns WebSocket URL for the client to connect.
    """
    call_id = str(uuid.uuid4())
    
    logger.info("starting_voice_call", call_id=call_id)

    try:
        ws_url = f"/voice/ws/{call_id}"
        expires_at = datetime.utcnow().isoformat()
        
        return StartCallResponse(
            ws_url=ws_url,
            call_id=call_id,
            expires_at=expires_at,
        )

    except Exception as e:
        logger.error("failed_to_start_call", call_id=call_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to start call: {str(e)}")


@router.websocket("/ws/{call_id}")
async def websocket_endpoint(websocket: WebSocket, call_id: str):
    """WebSocket endpoint for voice communication."""
    await websocket.accept()
    _active_connections[call_id] = websocket
    
    logger.info("websocket_connected", call_id=call_id)

    try:
        await create_simple_bot(websocket, call_id)
            
    except WebSocketDisconnect:
        logger.info("websocket_disconnected", call_id=call_id)
    except Exception as e:
        logger.error("websocket_error", call_id=call_id, error=str(e))
    finally:
        if call_id in _active_connections:
            del _active_connections[call_id]


@router.get("/health")
async def voice_health():
    """Health check for voice service."""
    return {
        "status": "healthy",
        "service": "pipecat-voice",
        "timestamp": datetime.utcnow().isoformat(),
    }
