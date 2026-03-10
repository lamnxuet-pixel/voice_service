"""Standalone Pipecat WebSocket server for voice bot."""

import asyncio
import json
from typing import Any

import structlog
from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_response import (
    LLMAssistantContextAggregator,
    LLMUserContextAggregator,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.ai_services import LLMContext
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.transports.network.websocket_server import (
    WebsocketServerParams,
    WebsocketServerTransport,
)

from app.config import settings
from app.database import async_session_factory
from app.prompts.patient_registration import SYSTEM_PROMPT
from app.services import session_service

logger = structlog.get_logger()

# Global Gemini client
from google import genai
_gemini_client = genai.Client(api_key=settings.gemini_api_key)


class GeminiLLMService(FrameProcessor):
    """Custom LLM service that integrates Gemini with Pipecat."""

    def __init__(self, call_id: str):
        super().__init__()
        self.call_id = call_id
        self._conversation_history: list[dict[str, Any]] = []
        self._max_history_turns = 20

    async def process_frame(self, frame, direction: FrameDirection):
        """Process incoming frames and generate responses."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            user_text = frame.text
            logger.info("user_message", call_id=self.call_id, text=user_text)

            self._conversation_history.append({"role": "user", "content": user_text})
            await self._generate_response()

        await self.push_frame(frame, direction)

    async def _generate_response(self):
        """Generate a response from Gemini."""
        from google.genai import types
        from app.prompts.patient_registration import TOOLS

        contents = self._build_gemini_contents()

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=TOOLS,
            temperature=0.7,
            max_output_tokens=512,
            top_p=0.95,
            top_k=40,
        )

        try:
            await self.push_frame(LLMFullResponseStartFrame())

            response_text = ""
            tool_calls = []

            stream = await _gemini_client.aio.models.generate_content_stream(
                model="gemini-2.5-flash-lite-preview-09-2025",
                contents=contents,
                config=config,
            )
            async for chunk in stream:
                if not chunk.candidates:
                    continue

                candidate = chunk.candidates[0]
                if not candidate.content or not candidate.content.parts:
                    continue

                for part in candidate.content.parts:
                    if part.text:
                        response_text += part.text
                        await self.push_frame(TextFrame(text=part.text))
                    elif part.function_call:
                        fc = part.function_call
                        tool_calls.append({
                            "name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                        })

            assistant_msg = {"role": "assistant", "content": response_text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self._conversation_history.append(assistant_msg)

            if tool_calls:
                await self._execute_tools(tool_calls)

            await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error("gemini_error", call_id=self.call_id, error=str(e))
            await self.push_frame(TextFrame(text="I'm sorry, I'm experiencing a technical issue."))
            await self.push_frame(LLMFullResponseEndFrame())

    async def _execute_tools(self, tool_calls: list[dict]):
        """Execute tool calls."""
        from app.services.tool_executor import execute_tools

        async with async_session_factory() as db:
            batch_calls = [
                {"name": tc["name"], "arguments": tc["arguments"]}
                for tc in tool_calls
            ]

            results = await execute_tools(
                call_id=self.call_id,
                db=db,
                tool_calls=batch_calls,
                timeout=5.0,
            )

            for tool_call, result in zip(tool_calls, results):
                self._conversation_history.append({
                    "role": "tool",
                    "name": tool_call["name"],
                    "content": json.dumps(result),
                })

            if len(self._conversation_history) > self._max_history_turns:
                self._conversation_history = self._conversation_history[-self._max_history_turns:]

            await self._generate_response()

    def _build_gemini_contents(self):
        """Build Gemini contents from history."""
        from google.genai import types

        contents = []
        for msg in self._conversation_history:
            role = msg.get("role", "user")

            if role == "assistant":
                gemini_role = "model"
            elif role in ("system", "user"):
                gemini_role = "user"
            elif role == "tool":
                gemini_role = "user"
            else:
                gemini_role = "user"

            content_text = msg.get("content", "")

            if role == "tool":
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

            if role == "assistant" and msg.get("tool_calls"):
                parts = []
                if content_text:
                    parts.append(types.Part.from_text(text=content_text))
                for tc in msg["tool_calls"]:
                    parts.append(types.Part.from_function_call(
                        name=tc["name"],
                        args=tc["arguments"],
                    ))
                contents.append(types.Content(role="model", parts=parts))
                continue

            if content_text:
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=content_text)],
                ))

        return contents


async def run_bot(websocket, call_id: str):
    """Run the bot for a single connection."""
    logger.info("bot_started", call_id=call_id)

    session_service.get_or_create_session(call_id)

    transport = WebsocketServerTransport(
        websocket=websocket,
        params=WebsocketServerParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        model="nova-2",
        language="en-US",
    )

    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        voice="aura-helios-en",
    )

    llm = GeminiLLMService(call_id=call_id)

    context = LLMContext()
    user_response = LLMUserContextAggregator(context)
    assistant_response = LLMAssistantContextAggregator(context)

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_response,
        llm,
        tts,
        transport.output(),
        assistant_response,
    ])

    task = PipelineTask(pipeline)

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info("client_connected", call_id=call_id)
        await task.queue_frames([
            TextFrame(text="Hello! I'm Alex, your patient intake coordinator. May I have your first and last name?")
        ])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info("client_disconnected", call_id=call_id)
        session_service.delete_session(call_id)
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner()
    await runner.run(task)

    logger.info("bot_finished", call_id=call_id)


async def main():
    """Start the WebSocket server."""
    import websockets
    import uuid

    async def handler(websocket):
        call_id = str(uuid.uuid4())
        try:
            await run_bot(websocket, call_id)
        except Exception as e:
            logger.error("bot_error", call_id=call_id, error=str(e))

    async with websockets.serve(handler, "localhost", 8765):
        logger.info("websocket_server_started", host="localhost", port=8765)
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())
