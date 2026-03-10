"""LLM streaming endpoint — Vapi Custom LLM mode calls this."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import gemini_service, session_service

logger = structlog.get_logger()

router = APIRouter(tags=["llm"])


@router.post("/llm")
async def llm_endpoint(request: Request):
    """
    Vapi Custom LLM endpoint.

    Receives the full conversation history each turn,
    streams back an OpenAI-compatible SSE response.
    """
    body = await request.json()
    messages = body.get("messages", [])
    call_info = body.get("call", {})
    call_id = call_info.get("id", "unknown")

    logger.info("llm_request_received", call_id=call_id, message_count=len(messages))

    # Ensure session exists for this call
    session_service.get_or_create_session(call_id)

    # Stream the Gemini response as SSE
    return StreamingResponse(
        gemini_service.stream_completion(messages, call_id=call_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.options("/llm")
async def llm_options():
    """Handle CORS preflight for the LLM endpoint."""
    return {"status": "ok"}
