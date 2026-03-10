"""Simplified Pipecat bot with direct Deepgram WebSocket integration."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import structlog
from fastapi import WebSocket

from app.config import settings
from app.database import async_session_factory
from app.prompts.patient_registration import SYSTEM_PROMPT
from app.services import patient_service, session_service

logger = structlog.get_logger()

# Global Gemini client for connection reuse
from google import genai
from google.genai import types

# Initialize async-capable client
_gemini_client = genai.Client(api_key=settings.gemini_api_key)


class SimplePipecatBot:
    """Simplified bot that handles WebSocket audio directly with Deepgram."""
    
    def __init__(self, websocket: WebSocket, call_id: str):
        self.websocket = websocket
        self.call_id = call_id
        self.deepgram_ws = None
        self.conversation_history: list[dict[str, Any]] = []
        self._max_history_turns = 20
        self.is_running = True
        
    async def start(self):
        """Start the bot and handle the conversation."""
        logger.info("starting_simple_bot", call_id=self.call_id)
        
        # Create session
        session_service.get_or_create_session(self.call_id)
        
        # Connect to Deepgram STT
        await self._connect_deepgram()
        
        # Send greeting
        await self._speak("Hello! I'm Alex, your patient intake coordinator. I'm here to help you register. May I have your first and last name?")
        
        # Start listening for audio
        try:
            audio_received = False
            while self.is_running:
                # Receive audio from client
                data = await self.websocket.receive()
                
                if "bytes" in data:
                    audio_received = True
                    # Forward audio to Deepgram
                    if self.deepgram_ws:
                        try:
                            await self.deepgram_ws.send(data["bytes"])
                            logger.debug("audio_forwarded", call_id=self.call_id, size=len(data["bytes"]))
                        except Exception as e:
                            logger.error("audio_forward_error", call_id=self.call_id, error=str(e))
                            # Reconnect to Deepgram if connection lost
                            await self._reconnect_deepgram()
                elif "text" in data:
                    # Handle text messages (control messages)
                    try:
                        msg = json.loads(data["text"])
                        if msg.get("type") == "close":
                            break
                    except json.JSONDecodeError:
                        pass
            
            # If we received audio, send a finalize message to Deepgram
            if audio_received and self.deepgram_ws:
                try:
                    await self.deepgram_ws.send(json.dumps({"type": "CloseStream"}))
                    logger.info("deepgram_stream_closed", call_id=self.call_id)
                except Exception:
                    pass
                        
        except Exception as e:
            logger.error("bot_error", call_id=self.call_id, error=str(e))
        finally:
            await self.cleanup()
    
    async def _connect_deepgram(self):
        """Connect to Deepgram STT WebSocket."""
        import websockets
        
        deepgram_url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?model=nova-2"
            f"&language=en-US"
            f"&encoding=linear16"
            f"&sample_rate=16000"
            f"&channels=1"
            f"&interim_results=true"
            f"&endpointing=300"
            f"&vad_events=true"
        )
        
        # Use additional_headers instead of extra_headers
        self.deepgram_ws = await websockets.connect(
            deepgram_url,
            additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"}
        )
        
        # Start listening for transcriptions
        asyncio.create_task(self._listen_deepgram())
        
        # Start keepalive task to prevent timeout
        asyncio.create_task(self._keepalive_deepgram())
        
        logger.info("deepgram_connected", call_id=self.call_id)
    
    async def _reconnect_deepgram(self):
        """Reconnect to Deepgram if connection is lost."""
        logger.warning("reconnecting_deepgram", call_id=self.call_id)
        
        # Close existing connection
        if self.deepgram_ws:
            try:
                await self.deepgram_ws.close()
            except Exception:
                pass
        
        # Wait a bit before reconnecting
        await asyncio.sleep(1)
        
        # Reconnect
        try:
            await self._connect_deepgram()
            logger.info("deepgram_reconnected", call_id=self.call_id)
        except Exception as e:
            logger.error("deepgram_reconnect_failed", call_id=self.call_id, error=str(e))
    
    async def _keepalive_deepgram(self):
        """Send keepalive messages to Deepgram to prevent timeout."""
        try:
            while self.is_running and self.deepgram_ws:
                await asyncio.sleep(5)  # Send keepalive every 5 seconds
                if self.deepgram_ws:
                    try:
                        # Send a keepalive message (empty JSON)
                        await self.deepgram_ws.send(json.dumps({"type": "KeepAlive"}))
                        logger.debug("keepalive_sent", call_id=self.call_id)
                    except Exception as e:
                        logger.warning("keepalive_failed", call_id=self.call_id, error=str(e))
                        break
        except Exception as e:
            logger.error("keepalive_error", call_id=self.call_id, error=str(e))
    
    async def _listen_deepgram(self):
        """Listen for transcriptions from Deepgram."""
        try:
            async for message in self.deepgram_ws:
                data = json.loads(message)
                
                # Check if this is a final transcript
                if data.get("is_final"):
                    transcript = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                    
                    if transcript.strip():
                        logger.info("user_transcript", call_id=self.call_id, text=transcript)
                        
                        # Send transcript to client
                        await self._send_transcript("user", transcript)
                        
                        # Process the user's message
                        await self._handle_user_message(transcript)
                        
        except Exception as e:
            logger.error("deepgram_listen_error", call_id=self.call_id, error=str(e))
    
    async def _handle_user_message(self, text: str):
        """Handle user message and generate response."""
        # Add to history
        self.conversation_history.append({"role": "user", "content": text})
        
        # Generate response from Gemini
        await self._generate_response()
    
    async def _generate_response(self):
        """Generate response from Gemini."""
        from app.prompts.patient_registration import TOOLS
        
        # Build contents
        contents = self._build_gemini_contents()
        
        logger.debug("gemini_request", call_id=self.call_id, 
                    content_count=len(contents),
                    history_size=len(self.conversation_history))
        
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=TOOLS,
            temperature=0.7,
            max_output_tokens=512,
            top_p=0.95,
            top_k=40,
        )
        
        try:
            response_text = ""
            tool_calls = []
            
            # Stream response from Gemini (matches pattern in gemini_service.py)
            stream = await _gemini_client.aio.models.generate_content_stream(
                model="gemini-2.5-flash-lite-preview-09-2025",
                contents=contents,
                config=config,
            )
            
            logger.debug("stream_created", call_id=self.call_id, 
                        generator_type=type(stream).__name__)
            
            async for chunk in stream:
                if not chunk.candidates:
                    continue
                
                candidate = chunk.candidates[0]
                if not candidate.content or not candidate.content.parts:
                    continue
                
                for part in candidate.content.parts:
                    if part.text:
                        response_text += part.text
                    elif part.function_call:
                        fc = part.function_call
                        tool_calls.append({
                            "name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                        })
            
            # Speak the response
            if response_text:
                await self._speak(response_text)
                await self._send_transcript("assistant", response_text)
            
            # Add to history
            assistant_msg = {"role": "assistant", "content": response_text}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.conversation_history.append(assistant_msg)
            
            # Execute tools if any
            if tool_calls:
                await self._execute_tools(tool_calls)
                
        except Exception as e:
            logger.error("gemini_error", call_id=self.call_id, error=str(e))
            await self._speak("I'm sorry, I'm experiencing a technical issue. Could you please try again?")
    
    async def _speak(self, text: str):
        """Convert text to speech and send to client."""
        import httpx
        
        # Use Deepgram TTS
        url = "https://api.deepgram.com/v1/speak?model=aura-helios-en&encoding=linear16&sample_rate=16000"
        
        headers = {
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {"text": text}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                audio_data = response.content
                
                # Send as base64 to client
                audio_b64 = base64.b64encode(audio_data).decode('utf-8')
                await self.websocket.send_text(audio_b64)
                
                logger.info("audio_sent", call_id=self.call_id, size=len(audio_data))
            else:
                logger.error("tts_error", call_id=self.call_id, status=response.status_code)
    
    async def _send_transcript(self, role: str, text: str):
        """Send transcript to client."""
        try:
            msg = json.dumps({
                "type": "transcript",
                "role": role,
                "text": text,
                "timestamp": asyncio.get_event_loop().time()
            })
            await self.websocket.send_text(msg)
        except Exception as e:
            logger.warning("transcript_send_error", call_id=self.call_id, error=str(e))
    
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
                self.conversation_history.append({
                    "role": "tool",
                    "name": tool_call["name"],
                    "content": json.dumps(result),
                })
            
            # Truncate history
            if len(self.conversation_history) > self._max_history_turns:
                self.conversation_history = self.conversation_history[-self._max_history_turns:]
            
            # Generate follow-up
            await self._generate_response()
    
    def _build_gemini_contents(self):
        """Build Gemini contents from history."""
        from google.genai import types
        
        contents = []
        for msg in self.conversation_history:
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
    
    async def cleanup(self):
        """Cleanup resources."""
        self.is_running = False
        
        if self.deepgram_ws:
            try:
                # Send close message before closing connection
                await self.deepgram_ws.send(json.dumps({"type": "CloseStream"}))
                await asyncio.sleep(0.1)  # Give it a moment to process
            except Exception:
                pass
            
            try:
                await self.deepgram_ws.close()
            except Exception:
                pass
        
        session_service.delete_session(self.call_id)
        
        logger.info("bot_cleanup_complete", call_id=self.call_id)


async def create_simple_bot(websocket: WebSocket, call_id: str):
    """Create and run a simple bot."""
    bot = SimplePipecatBot(websocket, call_id)
    await bot.start()
