"""Pipecat bot service — handles voice conversation with Gemini."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import structlog
from pipecat.frames.frames import (
    EndFrame,
    Frame,
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
from pipecat.services.ai_services import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.transports.network.fastapi_websocket import FastAPIWebsocketParams, FastAPIWebsocketTransport

from app.config import settings
from app.database import async_session_factory
from app.prompts.patient_registration import SYSTEM_PROMPT
from app.services import patient_service, session_service

logger = structlog.get_logger()

# Global Gemini client for connection reuse (Performance optimization)
from google import genai
_gemini_client = genai.Client(api_key=settings.gemini_api_key)


class AudioOutputBridge(FrameProcessor):
    """Bridge to send audio frames directly to WebSocket client."""
    
    def __init__(self, websocket, call_id: str):
        super().__init__()
        self.websocket = websocket
        self.call_id = call_id
        self._frame_count = 0
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Intercept audio frames and send to WebSocket."""
        await super().process_frame(frame, direction)
        
        # Import here to avoid circular dependency
        from pipecat.frames.frames import AudioRawFrame
        
        # If it's an audio frame, send it to the WebSocket
        if isinstance(frame, AudioRawFrame):
            try:
                self._frame_count += 1
                # Send audio data as binary
                import base64
                audio_b64 = base64.b64encode(frame.audio).decode('utf-8')
                await self.websocket.send_text(audio_b64)
                
                if self._frame_count == 1:
                    # Log first frame details
                    logger.info("first_audio_frame", call_id=self.call_id, 
                               size=len(frame.audio),
                               sample_rate=getattr(frame, 'sample_rate', 'unknown'),
                               num_channels=getattr(frame, 'num_channels', 'unknown'))
                
                logger.debug("audio_sent_to_client", call_id=self.call_id, 
                           frame_num=self._frame_count, size=len(frame.audio))
            except Exception as e:
                logger.error("failed_to_send_audio", call_id=self.call_id, error=str(e))
        
        # Pass frame through
        await self.push_frame(frame, direction)


class GeminiLLMService(FrameProcessor):
    """Custom LLM service that integrates Gemini with Pipecat."""

    def __init__(self, call_id: str, websocket=None):
        super().__init__()
        self.call_id = call_id
        self.websocket = websocket
        self._conversation_history: list[dict[str, Any]] = []
        self._max_history_turns = 20  # Limit conversation history for performance

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process incoming frames and generate responses."""
        await super().process_frame(frame, direction)

        # Handle user transcription
        if isinstance(frame, TranscriptionFrame):
            user_text = frame.text
            logger.info("user_message", call_id=self.call_id, text=user_text, frame_type=type(frame).__name__)

            # Send transcript to WebSocket client
            await self._send_transcript("user", user_text)

            # Add to conversation history
            self._conversation_history.append({"role": "user", "content": user_text})

            # Generate response from Gemini
            await self._generate_response()

        # Pass through other frames
        await self.push_frame(frame, direction)

    async def _generate_response(self):
        """Generate a response from Gemini and push it to the pipeline."""
        import time
        start_time = time.time()
        
        from google.genai import types

        from app.prompts.patient_registration import TOOLS

        # Build Gemini contents
        contents = self._build_gemini_contents()

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=TOOLS,
            temperature=0.7,
            max_output_tokens=512,  # Reduced for faster responses
            top_p=0.95,  # Nucleus sampling for speed
            top_k=40,    # Limit token selection
        )

        try:
            # Signal start of response
            await self.push_frame(LLMFullResponseStartFrame())

            response_text = ""
            tool_calls = []

            # Stream response from Gemini using global client
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
                    # Handle text response
                    if part.text:
                        response_text += part.text
                        # Push text frame for TTS
                        await self.push_frame(TextFrame(text=part.text))

                    # Handle function call
                    elif part.function_call:
                        fc = part.function_call
                        tool_call = {
                            "name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                        }
                        tool_calls.append(tool_call)
                        logger.info("tool_call_detected", call_id=self.call_id, tool=fc.name)

            # Send bot transcript to WebSocket client
            if response_text:
                await self._send_transcript("assistant", response_text)

            # Add assistant response to history
            assistant_msg = {"role": "assistant", "content": response_text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self._conversation_history.append(assistant_msg)

            # Log performance metrics
            latency_ms = (time.time() - start_time) * 1000
            token_count = len(response_text.split())
            logger.info("llm_response_generated", 
                       call_id=self.call_id, 
                       latency_ms=round(latency_ms, 2),
                       token_count=token_count,
                       has_tool_calls=len(tool_calls) > 0)

            # Execute tool calls if any
            if tool_calls:
                await self._execute_tools(tool_calls)

            # Signal end of response
            await self.push_frame(LLMFullResponseEndFrame())

        except Exception as e:
            logger.error("gemini_error", call_id=self.call_id, error=str(e))
            await self.push_frame(TextFrame(text="I'm sorry, I'm experiencing a technical issue. Could you please try again?"))
            await self.push_frame(LLMFullResponseEndFrame())

    async def _execute_tools(self, tool_calls: list[dict]):
        """Execute tool calls using unified executor (toggleable workflow)."""
        from app.services.tool_executor import execute_tools
        
        async with async_session_factory() as db:
            # Prepare tool calls for execution
            batch_calls = [
                {
                    "name": tool_call["name"],
                    "arguments": tool_call["arguments"],
                }
                for tool_call in tool_calls
            ]
            
            # Execute using unified executor (auto-selects workflow mode)
            results = await execute_tools(
                call_id=self.call_id,
                db=db,
                tool_calls=batch_calls,
                timeout=5.0,
            )
            
            # Add all tool results to history
            for tool_call, result in zip(tool_calls, results):
                tool_name = tool_call["name"]
                
                # Log if fallback was used
                if result.get("fallback", False):
                    logger.warning(
                        "tool_fallback_used",
                        call_id=self.call_id,
                        tool_name=tool_name,
                    )
                
                self._conversation_history.append({
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps(result),
                })
            
            # Truncate history if needed
            self._truncate_history()
            
            # Generate single follow-up response after all tools complete
            await self._generate_response()

    async def _execute_single_tool(self, tool_name: str, arguments: dict, db) -> dict:
        """Execute a single tool and return the result."""
        from uuid import UUID

        from app.schemas.patient import PatientCreate, PatientUpdate

        if tool_name == "validate_field":
            field_name = arguments.get("field_name", "")
            field_value = arguments.get("field_value", "")
            
            # Validate using Pydantic schemas
            try:
                # Create a minimal test object with just this field
                test_data = {field_name: field_value}
                
                # Add required fields with dummy values for validation
                if field_name in ["first_name", "last_name", "date_of_birth", "sex", "phone_number", 
                                  "address_line_1", "city", "state", "zip_code"]:
                    # Required field validation
                    dummy_patient = {
                        "first_name": "Test",
                        "last_name": "User",
                        "date_of_birth": "01/01/1990",
                        "sex": "Male",
                        "phone_number": "5555555555",
                        "address_line_1": "123 Main St",
                        "city": "Boston",
                        "state": "MA",
                        "zip_code": "02101",
                    }
                    dummy_patient[field_name] = field_value
                    PatientCreate(**dummy_patient)
                else:
                    # Optional field validation
                    PatientUpdate(**test_data)
                
                return {
                    "valid": True,
                    "field_name": field_name,
                    "message": f"{field_name} is valid.",
                }
            except Exception as e:
                error_msg = str(e)
                # Extract the actual validation error from Pydantic
                if "Value error," in error_msg:
                    error_msg = error_msg.split("Value error,")[1].strip()
                return {
                    "valid": False,
                    "field_name": field_name,
                    "error": error_msg,
                    "message": f"Invalid {field_name}: {error_msg}",
                }

        elif tool_name == "update_field":
            field_name = arguments.get("field_name", "")
            field_value = arguments.get("field_value", "")
            
            draft = session_service.get_or_create_session(self.call_id)
            draft.collected[field_name] = field_value
            
            return {
                "result": "success",
                "field_name": field_name,
                "field_value": field_value,
                "message": f"Updated {field_name} to {field_value}.",
            }

        elif tool_name == "reset_registration":
            session_service.reset_session(self.call_id)
            
            return {
                "result": "success",
                "message": "Registration has been reset. Let's start over from the beginning.",
            }

        elif tool_name == "check_duplicate":
            phone_number = arguments.get("phone_number", "")
            existing = await patient_service.check_duplicate_by_phone(db, phone_number)
            if existing:
                draft = session_service.get_or_create_session(self.call_id)
                draft.is_update = True
                draft.patient_id = existing.id
                return {
                    "duplicate": True,
                    "patient_id": str(existing.id),
                    "existing_name": f"{existing.first_name} {existing.last_name}",
                    "message": f"A patient named {existing.first_name} {existing.last_name} already exists with this phone number.",
                }
            return {"duplicate": False, "message": "No existing patient found with this phone number."}

        elif tool_name == "save_patient":
            draft = session_service.get_or_create_session(self.call_id)
            
            # Idempotency check
            tool_call_id = f"{tool_name}_{self.call_id}"
            if draft.idempotency_key == tool_call_id:
                return {
                    "result": "already_saved",
                    "patient_id": str(draft.patient_id),
                    "message": "Patient was already saved successfully.",
                }

            patient_data = PatientCreate(**arguments)
            patient = await patient_service.create_patient(db, patient_data)
            await db.commit()

            session_service.mark_confirmed(self.call_id, patient.id, tool_call_id)

            return {
                "result": "success",
                "patient_id": str(patient.id),
                "message": f"Patient {patient.first_name} {patient.last_name} has been registered successfully.",
            }

        elif tool_name == "update_patient":
            draft = session_service.get_or_create_session(self.call_id)
            
            tool_call_id = f"{tool_name}_{self.call_id}"
            if draft.idempotency_key == tool_call_id:
                return {
                    "result": "already_updated",
                    "patient_id": str(draft.patient_id),
                    "message": "Patient was already updated successfully.",
                }

            patient_id_str = arguments.pop("patient_id", None)
            patient_id = UUID(patient_id_str)
            update_data = PatientUpdate(**arguments)
            
            patient = await patient_service.update_patient(db, patient_id, update_data)
            await db.commit()

            session_service.mark_confirmed(self.call_id, patient.id, tool_call_id)

            return {
                "result": "success",
                "patient_id": str(patient.id),
                "message": f"Patient {patient.first_name} {patient.last_name}'s information has been updated.",
            }

        elif tool_name == "schedule_appointment":
            patient_id_str = arguments.get("patient_id")
            preferred_day = arguments.get("preferred_day", "")
            preferred_time = arguments.get("preferred_time", "")
            
            # Enhanced mock scheduling with preferences
            import random
            from datetime import datetime, timedelta
            
            # Generate a realistic appointment time
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            times = {
                "morning": ["9:00 AM", "10:00 AM", "11:00 AM"],
                "afternoon": ["1:00 PM", "2:00 PM", "3:00 PM"],
                "evening": ["4:00 PM", "5:00 PM"],
            }
            
            # Use preferences or pick randomly
            if preferred_day and preferred_day in days:
                day = preferred_day
            else:
                day = random.choice(days)
            
            if preferred_time and preferred_time.lower() in times:
                time = random.choice(times[preferred_time.lower()])
            else:
                all_times = [t for time_list in times.values() for t in time_list]
                time = random.choice(all_times)
            
            # Calculate next occurrence of that day
            today = datetime.now()
            days_ahead = (days.index(day) - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next week
            appointment_date = today + timedelta(days=days_ahead)
            
            return {
                "result": "success",
                "appointment_day": day,
                "appointment_time": time,
                "appointment_date": appointment_date.strftime("%B %d, %Y"),
                "message": f"Appointment scheduled for {day}, {appointment_date.strftime('%B %d')} at {time}.",
            }

        return {"error": f"Unknown tool: {tool_name}"}

    async def _send_transcript(self, role: str, text: str):
        """Send transcript message to WebSocket client."""
        if self.websocket:
            try:
                transcript_msg = json.dumps({
                    "type": "transcript",
                    "role": role,
                    "text": text,
                    "timestamp": asyncio.get_event_loop().time()
                })
                await self.websocket.send_text(transcript_msg)
            except Exception as e:
                logger.warning("failed_to_send_transcript", call_id=self.call_id, error=str(e))

    def _truncate_history(self):
        """Truncate conversation history to prevent unbounded growth."""
        if len(self._conversation_history) > self._max_history_turns:
            # Keep only recent turns for performance
            self._conversation_history = self._conversation_history[-self._max_history_turns:]
            logger.info("conversation_history_truncated", call_id=self.call_id, turns=self._max_history_turns)
    
    def _build_gemini_contents(self):
        """Convert conversation history to Gemini Content objects."""
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

            # Handle tool results
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

            # Handle assistant messages with tool calls
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

            # Regular text message
            if content_text:
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=content_text)],
                ))

        return contents


async def create_pipecat_bot(websocket, call_id: str):
    """Create and run a Pipecat bot for a voice conversation."""
    logger.info("creating_pipecat_bot", call_id=call_id)

    # Create session
    session_service.get_or_create_session(call_id)

    # Initialize transport (WebSocket) with proper audio configuration
    # Note: FastAPIWebsocketTransport expects audio frames in a specific format
    # For browser-based raw PCM, we need to handle the conversion
    transport = FastAPIWebsocketTransport(
        websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,  # Don't add WAV header for raw PCM
            vad_analyzer=None,
            audio_in_sample_rate=16000,  # Match frontend sample rate
            audio_out_sample_rate=16000,  # Standard sample rate
            audio_out_channels=1,  # Mono audio
            serializer=None,  # No serializer for raw audio
        ),
    )
    
    logger.info("transport_configured", call_id=call_id, 
                audio_in=True, audio_out=True, sample_rate=16000)

    # Initialize STT service (Deepgram with optimized settings)
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        model="nova-2",  # Latest, most accurate model
        language="en-US",
        endpointing=1800,  # Wait 1000ms (1 second) of silence before ending turn
        interim_results=True,  # Get interim results for better responsiveness
    )

    # Initialize TTS service (Deepgram with natural voice)
    tts = DeepgramTTSService(
        api_key=settings.deepgram_api_key,
        voice="aura-helios-en",  # Natural, conversational voice
    )

    # Initialize custom Gemini LLM service
    llm = GeminiLLMService(call_id=call_id, websocket=websocket)
    
    # Create audio output bridge to send audio to client
    audio_bridge = AudioOutputBridge(websocket=websocket, call_id=call_id)

    # Create context and aggregators
    context = LLMContext()
    user_response = LLMUserContextAggregator(context)
    assistant_response = LLMAssistantContextAggregator(context)

    # Build pipeline with audio bridge
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_response,
            llm,
            tts,
            audio_bridge,  # Add bridge before transport output
            transport.output(),
            assistant_response,
        ]
    )

    # Create task
    task = PipelineTask(pipeline)

    # Setup event handlers
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client_connected", call_id=call_id)
        # Greet the caller
        await task.queue_frames([TextFrame(text="Hello! I'm Alex, your patient intake coordinator. I'm here to help you register. May I have your first and last name?")])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client_disconnected", call_id=call_id)
        # Cleanup session
        session_service.delete_session(call_id)
        await task.queue_frames([EndFrame()])
    
    @transport.event_handler("on_client_audio_received")
    async def on_client_audio_received(transport, audio_data):
        logger.debug("audio_received", call_id=call_id, size=len(audio_data) if audio_data else 0)

    # Run the pipeline
    runner = PipelineRunner()
    await runner.run(task)

    logger.info("pipecat_bot_finished", call_id=call_id)
