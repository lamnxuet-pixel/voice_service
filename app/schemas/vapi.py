"""Pydantic schemas for Vapi webhook and tool call payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# --- Vapi Custom LLM Request ---

class VapiMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class VapiLLMRequest(BaseModel):
    """Payload Vapi sends to the custom LLM endpoint (POST /llm)."""
    model: Optional[str] = None
    messages: list[VapiMessage]
    call: Optional[dict[str, Any]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = True


# --- Vapi Tool Call Request ---

class ToolCallFunction(BaseModel):
    name: str
    arguments: dict[str, Any]


class ToolCallPayload(BaseModel):
    id: str
    type: str = "function"
    function: ToolCallFunction


class VapiToolRequest(BaseModel):
    """Payload Vapi sends when a tool needs execution (POST /vapi/tool)."""
    message: dict[str, Any]


# --- Vapi Webhook Events ---

class VapiWebhookEvent(BaseModel):
    """Generic Vapi server message / webhook event."""
    message: dict[str, Any]
