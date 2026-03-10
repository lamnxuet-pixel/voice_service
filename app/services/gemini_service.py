"""Gemini streaming service — handles completions via Google GenAI SDK."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import structlog
from google import genai
from google.genai import types

from app.config import settings
from app.prompts.patient_registration import SYSTEM_PROMPT, TOOLS

logger = structlog.get_logger()

# Initialize the Gemini client (singleton for connection reuse)
client = genai.Client(api_key=settings.gemini_api_key)

MODEL = "gemini-2.5-flash-lite-preview-09-2025"
MAX_HISTORY_TURNS = 20  # Limit conversation history for performance


def _build_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Convert Vapi/OpenAI-style messages into Gemini Content objects."""
    contents: list[types.Content] = []
    for msg in messages:
        role = msg.get("role", "user")
        # Map OpenAI roles to Gemini roles
        if role == "assistant":
            gemini_role = "model"
        elif role in ("system", "user"):
            gemini_role = "user"
        elif role == "tool":
            # Tool result messages
            gemini_role = "user"
        else:
            gemini_role = "user"

        content_text = msg.get("content", "")

        # Handle tool call results
        if role == "tool" and msg.get("tool_call_id"):
            # Build a function response part
            tool_name = msg.get("name", "unknown")
            try:
                result_data = json.loads(content_text) if content_text else {}
            except (json.JSONDecodeError, TypeError):
                result_data = {"result": content_text}
            part = types.Part.from_function_response(
                name=tool_name,
                response=result_data,
            )
            contents.append(types.Content(role=gemini_role, parts=[part]))
            continue

        # Handle assistant messages with tool calls
        if role == "assistant" and msg.get("tool_calls"):
            parts = []
            if content_text:
                parts.append(types.Part.from_text(text=content_text))
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                func_name = func.get("name", "")
                try:
                    func_args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    func_args = {}
                # Create function call part with thought_signature
                fc_part = types.Part.from_function_call(
                    name=func_name,
                    args=func_args,
                )
                # Add thought_signature to comply with Gemini API requirements
                if not hasattr(fc_part, 'thought_signature') or fc_part.thought_signature is None:
                    fc_part.thought_signature = f"Calling {func_name} with provided arguments"
                parts.append(fc_part)
            contents.append(types.Content(role="model", parts=parts))
            continue

        # Regular text message
        if content_text:
            contents.append(types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=content_text)],
            ))

    return contents


async def stream_completion(
    messages: list[dict[str, Any]],
    call_id: str | None = None,
) -> AsyncIterator[str]:
    """
    Stream a Gemini completion as SSE-formatted chunks (OpenAI-compatible).

    Yields SSE lines: `data: {...}\n\n`
    """
    # Truncate history for performance
    if len(messages) > MAX_HISTORY_TURNS:
        messages = messages[-MAX_HISTORY_TURNS:]
        logger.info("conversation_history_truncated", call_id=call_id, turns=MAX_HISTORY_TURNS)
    
    contents = _build_contents(messages)

    logger.info("gemini_stream_start", call_id=call_id, message_count=len(messages))

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,
        temperature=0.7,
        max_output_tokens=512,  # Reduced for faster responses
        top_p=0.95,  # Nucleus sampling for speed
        top_k=40,    # Limit token selection
        response_modalities=["TEXT"],  # Explicitly set response modality
        thought_signature=True,  # Enable thought signatures for function calls
    )

    try:
        stream = await client.aio.models.generate_content_stream(
            model=MODEL,
            contents=contents,
            config=config,
        )
        async for chunk in stream:
            # Process each candidate
            if not chunk.candidates:
                continue

            candidate = chunk.candidates[0]
            if not candidate.content or not candidate.content.parts:
                continue

            for part in candidate.content.parts:
                # Text chunk
                if part.text:
                    sse_data = {
                        "id": f"chatcmpl-{call_id or 'unknown'}",
                        "object": "chat.completion.chunk",
                        "choices": [{
                            "index": 0,
                            "delta": {"role": "assistant", "content": part.text},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(sse_data)}\n\n"

                # Function call chunk
                elif part.function_call:
                    fc = part.function_call
                    sse_data = {
                        "id": f"chatcmpl-{call_id or 'unknown'}",
                        "object": "chat.completion.chunk",
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [{
                                    "id": f"call_{fc.name}_{call_id or 'auto'}",
                                    "type": "function",
                                    "function": {
                                        "name": fc.name,
                                        "arguments": json.dumps(dict(fc.args) if fc.args else {}),
                                    },
                                }],
                            },
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(sse_data)}\n\n"

        # Send the final [DONE] marker
        stop_data = {
            "id": f"chatcmpl-{call_id or 'unknown'}",
            "object": "chat.completion.chunk",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(stop_data)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error("gemini_stream_error", call_id=call_id, error=str(e))
        error_data = {
            "id": f"chatcmpl-{call_id or 'unknown'}",
            "object": "chat.completion.chunk",
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "I'm sorry, I'm experiencing a technical issue. Could you please try again?"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"
