"""Tool workflow orchestration with fallback mechanisms and default responses."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.patient import PatientCreate, PatientUpdate
from app.services import patient_service, session_service

logger = structlog.get_logger()


class ToolWorkflow:
    """Orchestrates tool execution with fallback mechanisms and performance optimizations."""

    def __init__(self, call_id: str, db: AsyncSession):
        self.call_id = call_id
        self.db = db
        self.execution_log: list[dict[str, Any]] = []
        
        # Performance optimizations
        self._session_cache: Optional[Any] = None  # Cache session to avoid repeated lookups
        self._duplicate_cache: dict[str, Optional[Any]] = {}  # Cache duplicate checks by phone
        self._progress_cache: Optional[dict] = None  # Cache progress calculations
        self._transcript_buffer: list[dict] = []  # Buffer transcript turns
        self._performance_metrics = {
            "cache_hits": 0,
            "cache_misses": 0,
            "db_queries": 0,
            "tools_executed": 0,
        }

    def _get_session(self):
        """Get session with caching to avoid repeated lookups."""
        if self._session_cache is None:
            self._performance_metrics["cache_misses"] += 1
            self._session_cache = session_service.get_or_create_session(self.call_id)
            logger.debug("session_cache_miss", call_id=self.call_id)
        else:
            self._performance_metrics["cache_hits"] += 1
        return self._session_cache

    def _invalidate_session_cache(self):
        """Invalidate session cache when session is modified."""
        self._session_cache = None
        self._progress_cache = None  # Progress depends on session

    def _invalidate_progress_cache(self):
        """Invalidate progress cache when fields are updated."""
        self._progress_cache = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """
        Execute a tool with timeout and fallback handling.
        
        Returns a result dict that always has a valid response,
        even if the tool fails.
        """
        start_time = asyncio.get_event_loop().time()
        self._performance_metrics["tools_executed"] += 1
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                self._route_tool(tool_name, arguments),
                timeout=timeout,
            )
            
            # Log success
            execution_time = asyncio.get_event_loop().time() - start_time
            self._log_execution(tool_name, arguments, result, execution_time, success=True)
            
            return result
            
        except asyncio.TimeoutError:
            logger.error("tool_timeout", call_id=self.call_id, tool_name=tool_name, timeout=timeout)
            fallback = self._get_fallback_response(tool_name, arguments, "timeout")
            self._log_execution(tool_name, arguments, fallback, timeout, success=False, error="timeout")
            return fallback
            
        except Exception as e:
            logger.error("tool_execution_failed", call_id=self.call_id, tool_name=tool_name, error=str(e))
            fallback = self._get_fallback_response(tool_name, arguments, str(e))
            execution_time = asyncio.get_event_loop().time() - start_time
            self._log_execution(tool_name, arguments, fallback, execution_time, success=False, error=str(e))
            return fallback

    async def _route_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route tool call to appropriate handler."""
        
        handlers: dict[str, Callable] = {
            # Original tools
            "validate_field": self._handle_validate_field,
            "check_duplicate": self._handle_check_duplicate,
            "update_field": self._handle_update_field,
            "save_patient": self._handle_save_patient,
            "update_patient": self._handle_update_patient,
            "reset_registration": self._handle_reset_registration,
            "schedule_appointment": self._handle_schedule_appointment,
            # New P0 tools
            "start_call": self._handle_start_call,
            "get_progress": self._handle_get_progress,
            "confirm_ready": self._handle_confirm_ready,
            "confirm_completed": self._handle_confirm_completed,
            "save_turn": self._handle_save_turn,
            "end_call": self._handle_end_call,
        }
        
        handler = handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        return await handler(arguments)

    # ==================== Tool Handlers ====================

    async def _handle_validate_field(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate a single field in real-time."""
        field_name = arguments.get("field_name", "")
        field_value = arguments.get("field_value", "")
        
        if not field_name or field_value is None:
            return {
                "valid": False,
                "field_name": field_name,
                "error": "field_name and field_value are required",
                "message": "Missing required parameters for validation.",
            }
        
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

    async def _handle_check_duplicate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check if a patient with this phone number already exists (with caching)."""
        phone_number = arguments.get("phone_number", "")
        
        if not phone_number:
            return {
                "duplicate": False,
                "error": "phone_number is required",
                "message": "Unable to check for duplicates without a phone number.",
            }
        
        # Check cache first
        if phone_number in self._duplicate_cache:
            self._performance_metrics["cache_hits"] += 1
            existing = self._duplicate_cache[phone_number]
            logger.debug("duplicate_cache_hit", call_id=self.call_id, phone=phone_number)
        else:
            self._performance_metrics["cache_misses"] += 1
            self._performance_metrics["db_queries"] += 1
            existing = await patient_service.check_duplicate_by_phone(self.db, phone_number)
            self._duplicate_cache[phone_number] = existing
            logger.debug("duplicate_cache_miss", call_id=self.call_id, phone=phone_number)
        
        if existing:
            # Mark session as update mode
            draft = self._get_session()
            draft.is_update = True
            draft.patient_id = existing.id
            
            return {
                "duplicate": True,
                "patient_id": str(existing.id),
                "existing_name": f"{existing.first_name} {existing.last_name}",
                "message": f"A patient named {existing.first_name} {existing.last_name} already exists with this phone number.",
            }
        
        return {
            "duplicate": False,
            "message": "No existing patient found with this phone number.",
        }

    async def _handle_update_field(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update a single field in the current registration draft."""
        field_name = arguments.get("field_name", "")
        field_value = arguments.get("field_value")
        
        if not field_name:
            return {
                "result": "error",
                "error": "field_name is required",
                "message": "Unable to update field without a field name.",
            }
        
        draft = self._get_session()
        draft.collected[field_name] = field_value
        
        # Invalidate caches that depend on collected fields
        self._invalidate_progress_cache()
        
        return {
            "result": "success",
            "field_name": field_name,
            "field_value": field_value,
            "message": f"Updated {field_name}.",
        }

    async def _handle_save_patient(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Save a new patient — with confirmation and idempotency checks."""
        draft = self._get_session()
        
        # NEW: Check confirmation happened
        if not draft.confirmed:
            return {
                "result": "error",
                "error": "not_confirmed",
                "message": "Please confirm all information is correct before saving. Read back the information and ask for confirmation.",
            }
        
        # NEW: Validate all required fields present
        required_fields = [
            "first_name", "last_name", "date_of_birth", "sex",
            "phone_number", "address_line_1", "city", "state", "zip_code"
        ]
        missing = [f for f in required_fields if f not in arguments or not arguments[f]]
        
        if missing:
            return {
                "result": "error",
                "error": "missing_required_fields",
                "missing_fields": missing,
                "message": f"Missing required fields: {', '.join(missing)}. Please collect these before saving.",
            }
        
        # Idempotency: prevent double-write
        tool_call_id = f"save_patient_{self.call_id}"
        if draft.idempotency_key == tool_call_id:
            logger.info("idempotent_save_skipped", call_id=self.call_id)
            return {
                "result": "already_saved",
                "patient_id": str(draft.patient_id),
                "message": "Patient was already saved successfully.",
            }
        
        # Validate and create
        try:
            patient_data = PatientCreate(**arguments)
        except Exception as e:
            return {
                "result": "error",
                "error": str(e),
                "message": "Unable to save patient due to validation errors. Please check the information provided.",
            }
        
        try:
            self._performance_metrics["db_queries"] += 1
            patient = await patient_service.create_patient(self.db, patient_data)
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            if "patients_phone_unique" in str(e):
                return {
                    "result": "error",
                    "error": "duplicate_phone",
                    "message": "A patient with this phone number already exists. Would you like to update their information instead?",
                }
            return {
                "result": "error",
                "error": str(e),
                "message": "I'm having trouble saving the patient information right now. Let me try again.",
            }
        
        # Mark session as confirmed
        session_service.mark_confirmed(self.call_id, patient.id, tool_call_id)
        self._invalidate_session_cache()
        
        return {
            "result": "success",
            "patient_id": str(patient.id),
            "message": f"Patient {patient.first_name} {patient.last_name} has been registered successfully.",
        }

    async def _handle_update_patient(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Update an existing patient — with idempotency check."""
        draft = session_service.get_or_create_session(self.call_id)
        
        # Idempotency check
        tool_call_id = f"update_patient_{self.call_id}"
        if draft.idempotency_key == tool_call_id:
            logger.info("idempotent_update_skipped", call_id=self.call_id)
            return {
                "result": "already_updated",
                "patient_id": str(draft.patient_id),
                "message": "Patient was already updated successfully.",
            }
        
        patient_id_str = arguments.pop("patient_id", None)
        if not patient_id_str:
            return {
                "result": "error",
                "error": "patient_id is required",
                "message": "Unable to update patient without an ID.",
            }
        
        try:
            patient_id = UUID(patient_id_str)
        except ValueError:
            return {
                "result": "error",
                "error": "Invalid patient_id format",
                "message": "There was an issue with the patient ID. Let me try again.",
            }
        
        # Validate update data
        try:
            update_data = PatientUpdate(**arguments)
        except Exception as e:
            return {
                "result": "error",
                "error": str(e),
                "message": "Unable to update patient due to validation errors.",
            }
        
        try:
            patient = await patient_service.update_patient(self.db, patient_id, update_data)
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            return {
                "result": "error",
                "error": str(e),
                "message": "I'm having trouble updating the patient information right now. Let me try again.",
            }
        
        if patient is None:
            return {
                "result": "error",
                "error": "Patient not found",
                "message": "I couldn't find that patient record. Let me check again.",
            }
        
        # Mark session
        session_service.mark_confirmed(self.call_id, patient.id, tool_call_id)
        
        return {
            "result": "success",
            "patient_id": str(patient.id),
            "message": f"Patient {patient.first_name} {patient.last_name}'s information has been updated.",
        }

    async def _handle_reset_registration(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reset the registration session and start over."""
        session_service.reset_session(self.call_id)
        
        return {
            "result": "success",
            "message": "Registration has been reset. Let's start over from the beginning.",
        }

    async def _handle_start_call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Initialize call tracking and check for incomplete registrations."""
        from datetime import datetime
        
        phone_number = arguments.get("phone_number", "")
        
        # Create or update call log
        draft = self._get_session()
        draft.collected["phone_number"] = phone_number
        
        # Check for incomplete registration (with caching)
        if phone_number:
            if phone_number in self._duplicate_cache:
                self._performance_metrics["cache_hits"] += 1
                existing = self._duplicate_cache[phone_number]
            else:
                self._performance_metrics["cache_misses"] += 1
                self._performance_metrics["db_queries"] += 1
                existing = await patient_service.check_duplicate_by_phone(self.db, phone_number)
                self._duplicate_cache[phone_number] = existing
            
            if existing:
                draft.is_update = True
                draft.patient_id = existing.id
                return {
                    "result": "existing_patient",
                    "patient_id": str(existing.id),
                    "patient_name": f"{existing.first_name} {existing.last_name}",
                    "message": f"Welcome back, {existing.first_name}! I have your information on file.",
                }
        
        return {
            "result": "new_call",
            "message": "Call started successfully.",
        }

    async def _handle_get_progress(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Get field collection progress (with caching)."""
        # Check cache first
        if self._progress_cache is not None:
            self._performance_metrics["cache_hits"] += 1
            return self._progress_cache
        
        self._performance_metrics["cache_misses"] += 1
        draft = self._get_session()
        
        required_fields = [
            "first_name", "last_name", "date_of_birth", "sex",
            "phone_number", "address_line_1", "city", "state", "zip_code"
        ]
        
        collected = draft.collected
        collected_required = [f for f in required_fields if f in collected and collected[f]]
        missing_required = [f for f in required_fields if f not in collected_required]
        
        optional_fields = [
            "email", "address_line_2", "insurance_provider", "insurance_member_id",
            "emergency_contact_name", "emergency_contact_phone", "preferred_language"
        ]
        collected_optional = [f for f in optional_fields if f in collected and collected[f]]
        
        progress_pct = int((len(collected_required) / len(required_fields)) * 100)
        
        result = {
            "result": "success",
            "progress_percentage": progress_pct,
            "required_fields_collected": len(collected_required),
            "required_fields_total": len(required_fields),
            "missing_required_fields": missing_required,
            "optional_fields_collected": collected_optional,
            "ready_for_confirmation": len(missing_required) == 0,
            "message": f"Collected {len(collected_required)} of {len(required_fields)} required fields.",
        }
        
        # Cache the result
        self._progress_cache = result
        
        return result

    async def _handle_confirm_ready(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate all required fields collected and mark ready for confirmation."""
        draft = session_service.get_or_create_session(self.call_id)
        
        required_fields = [
            "first_name", "last_name", "date_of_birth", "sex",
            "phone_number", "address_line_1", "city", "state", "zip_code"
        ]
        
        collected = draft.collected
        missing = [f for f in required_fields if f not in collected or not collected[f]]
        
        if missing:
            return {
                "result": "not_ready",
                "ready": False,
                "missing_fields": missing,
                "message": f"Still need: {', '.join(missing)}",
            }
        
        # Mark as ready for confirmation
        draft.collected["_confirmation_status"] = "ready"
        
        return {
            "result": "ready",
            "ready": True,
            "message": "All required fields collected. Ready to read back for confirmation.",
        }

    async def _handle_confirm_completed(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Mark that user has confirmed all fields are correct."""
        from datetime import datetime
        
        draft = session_service.get_or_create_session(self.call_id)
        
        # Mark as confirmed
        draft.confirmed = True
        draft.collected["_confirmation_status"] = "confirmed"
        draft.collected["_confirmation_timestamp"] = datetime.utcnow().isoformat()
        
        logger.info("confirmation_completed", call_id=self.call_id)
        
        return {
            "result": "success",
            "confirmed": True,
            "message": "Confirmation completed. Ready to save patient information.",
        }

    async def _handle_save_turn(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Save a conversation turn for transcript building (with buffering)."""
        speaker = arguments.get("speaker", "unknown")  # "user" or "agent"
        message = arguments.get("message", "")
        
        # Add to buffer instead of immediately writing to session
        from datetime import datetime
        turn = {
            "speaker": speaker,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._transcript_buffer.append(turn)
        
        # Flush buffer every 5 turns to reduce session updates
        if len(self._transcript_buffer) >= 5:
            await self._flush_transcript_buffer()
        
        return {
            "result": "success",
            "message": "Turn saved to transcript.",
        }

    async def _flush_transcript_buffer(self):
        """Flush buffered transcript turns to session."""
        if not self._transcript_buffer:
            return
        
        draft = self._get_session()
        
        # Initialize transcript if not exists
        if "_transcript" not in draft.collected:
            draft.collected["_transcript"] = []
        
        # Add all buffered turns
        draft.collected["_transcript"].extend(self._transcript_buffer)
        self._transcript_buffer = []
        
        logger.debug("transcript_buffer_flushed", call_id=self.call_id)

    async def _handle_end_call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Complete call tracking and save transcript."""
        from datetime import datetime
        
        outcome = arguments.get("outcome", "completed")  # completed, abandoned, failed, error
        summary = arguments.get("summary", "")
        
        # Flush any remaining buffered transcript turns
        await self._flush_transcript_buffer()
        
        draft = self._get_session()
        
        # Build transcript
        transcript_turns = draft.collected.get("_transcript", [])
        transcript_text = "\n".join([
            f"[{turn['timestamp']}] {turn['speaker']}: {turn['message']}"
            for turn in transcript_turns
        ])
        
        # Save to call_logs if patient was created
        if draft.patient_id:
            try:
                self._performance_metrics["db_queries"] += 1
                await patient_service.log_call(
                    db=self.db,
                    call_id=self.call_id,
                    patient_id=draft.patient_id,
                    transcript=transcript_text or summary,
                    status=outcome,
                    started_at=None,  # Would need to track this
                    ended_at=datetime.utcnow(),
                )
                await self.db.commit()
            except Exception as e:
                logger.error("failed_to_save_call_log", call_id=self.call_id, error=str(e))
        
        # Clean up session
        session_service.delete_session(self.call_id)
        self._invalidate_session_cache()
        
        logger.info("call_ended", call_id=self.call_id, outcome=outcome, patient_id=str(draft.patient_id) if draft.patient_id else None)
        
        return {
            "result": "success",
            "outcome": outcome,
            "transcript_saved": draft.patient_id is not None,
            "message": f"Call ended with outcome: {outcome}",
        }

    async def _handle_schedule_appointment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Enhanced appointment scheduling with preferences."""
        import random
        from datetime import datetime, timedelta
        
        patient_id_str = arguments.get("patient_id")
        if not patient_id_str:
            return {
                "result": "error",
                "error": "patient_id is required",
                "message": "I need a patient ID to schedule an appointment.",
            }
        
        preferred_day = arguments.get("preferred_day", "")
        preferred_time = arguments.get("preferred_time", "")
        
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
        
        logger.info("appointment_scheduled", call_id=self.call_id, patient_id=patient_id_str, day=day, time=time)
        
        return {
            "result": "success",
            "appointment_day": day,
            "appointment_time": time,
            "appointment_date": appointment_date.strftime("%B %d, %Y"),
            "message": f"Appointment scheduled for {day}, {appointment_date.strftime('%B %d')} at {time}.",
        }

    # ==================== Fallback Responses ====================

    def _get_fallback_response(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        error: str,
    ) -> dict[str, Any]:
        """
        Get a safe fallback response when a tool fails.
        
        These responses allow the conversation to continue gracefully
        even when tools fail.
        """
        fallbacks = {
            "validate_field": {
                "valid": True,  # Assume valid to continue conversation
                "field_name": arguments.get("field_name", "unknown"),
                "message": "I'll note that down. We can verify it later if needed.",
                "fallback": True,
            },
            "check_duplicate": {
                "duplicate": False,  # Assume no duplicate to continue
                "message": "I'll proceed with the registration.",
                "fallback": True,
            },
            "update_field": {
                "result": "success",
                "field_name": arguments.get("field_name", "unknown"),
                "field_value": arguments.get("field_value", ""),
                "message": "I've noted that change.",
                "fallback": True,
            },
            "save_patient": {
                "result": "error",
                "error": error,
                "message": "I'm having trouble saving the information right now. Could you please try calling back in a few minutes?",
                "fallback": True,
            },
            "update_patient": {
                "result": "error",
                "error": error,
                "message": "I'm having trouble updating the information right now. Could you please try calling back in a few minutes?",
                "fallback": True,
            },
            "reset_registration": {
                "result": "success",
                "message": "Let's start over. What's your first name?",
                "fallback": True,
            },
            "schedule_appointment": {
                "result": "success",
                "appointment_day": "Tuesday",
                "appointment_time": "10:00 AM",
                "appointment_date": "Next week",
                "message": "I'll have someone from our scheduling team call you to confirm an appointment time.",
                "fallback": True,
            },
            # New P0 tools
            "start_call": {
                "result": "new_call",
                "message": "Let's get started with your registration.",
                "fallback": True,
            },
            "get_progress": {
                "result": "success",
                "progress_percentage": 50,
                "ready_for_confirmation": False,
                "message": "We're making good progress.",
                "fallback": True,
            },
            "confirm_ready": {
                "result": "ready",
                "ready": True,
                "message": "Let me read back your information.",
                "fallback": True,
            },
            "confirm_completed": {
                "result": "success",
                "confirmed": True,
                "message": "Thank you for confirming.",
                "fallback": True,
            },
            "save_turn": {
                "result": "success",
                "message": "Noted.",
                "fallback": True,
            },
            "end_call": {
                "result": "success",
                "outcome": "completed",
                "message": "Thank you for calling!",
                "fallback": True,
            },
        }
        
        fallback = fallbacks.get(tool_name, {
            "error": error,
            "message": "I'm experiencing a technical issue. Let me try that again.",
            "fallback": True,
        })
        
        logger.warning(
            "fallback_response_used",
            call_id=self.call_id,
            tool_name=tool_name,
            error=error,
        )
        
        return fallback

    def _log_execution(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        execution_time: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Log tool execution for monitoring and debugging."""
        log_entry = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "execution_time_ms": round(execution_time * 1000, 2),
            "success": success,
            "error": error,
            "fallback_used": result.get("fallback", False),
        }
        
        self.execution_log.append(log_entry)
        
        logger.info(
            "tool_executed",
            call_id=self.call_id,
            tool_name=tool_name,
            success=success,
            execution_time_ms=log_entry["execution_time_ms"],
            fallback_used=log_entry["fallback_used"],
        )

    def get_execution_summary(self) -> dict[str, Any]:
        """Get a summary of all tool executions in this workflow."""
        total_tools = len(self.execution_log)
        successful_tools = sum(1 for log in self.execution_log if log["success"])
        fallback_tools = sum(1 for log in self.execution_log if log.get("fallback_used", False))
        total_time = sum(log["execution_time_ms"] for log in self.execution_log)
        
        # Calculate cache hit rate
        total_cache_ops = self._performance_metrics["cache_hits"] + self._performance_metrics["cache_misses"]
        cache_hit_rate = (
            self._performance_metrics["cache_hits"] / total_cache_ops
            if total_cache_ops > 0 else 0
        )
        
        return {
            "call_id": self.call_id,
            "total_tools_executed": total_tools,
            "successful_tools": successful_tools,
            "failed_tools": total_tools - successful_tools,
            "fallback_responses_used": fallback_tools,
            "total_execution_time_ms": round(total_time, 2),
            "average_execution_time_ms": round(total_time / total_tools, 2) if total_tools > 0 else 0,
            "execution_log": self.execution_log,
            # Performance metrics
            "cache_hit_rate": round(cache_hit_rate * 100, 1),
            "cache_hits": self._performance_metrics["cache_hits"],
            "cache_misses": self._performance_metrics["cache_misses"],
            "db_queries": self._performance_metrics["db_queries"],
        }


# ==================== Convenience Functions ====================

async def execute_tool_with_workflow(
    call_id: str,
    db: AsyncSession,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 5.0,
) -> dict[str, Any]:
    """
    Execute a single tool using the workflow system.
    
    This is a convenience function for single tool execution.
    """
    workflow = ToolWorkflow(call_id, db)
    return await workflow.execute_tool(tool_name, arguments, timeout)


async def execute_tools_batch(
    call_id: str,
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Execute multiple tools in parallel using the workflow system.
    
    Args:
        call_id: The call identifier
        db: Database session
        tool_calls: List of dicts with 'name' and 'arguments' keys
        timeout: Timeout per tool in seconds
    
    Returns:
        List of results in the same order as tool_calls
    """
    workflow = ToolWorkflow(call_id, db)
    
    tasks = [
        workflow.execute_tool(
            tool_call["name"],
            tool_call["arguments"],
            timeout,
        )
        for tool_call in tool_calls
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Convert exceptions to error responses
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            tool_name = tool_calls[i]["name"]
            fallback = workflow._get_fallback_response(
                tool_name,
                tool_calls[i]["arguments"],
                str(result),
            )
            processed_results.append(fallback)
        else:
            processed_results.append(result)
    
    # Log summary
    summary = workflow.get_execution_summary()
    logger.info("batch_execution_complete", **summary)
    
    return processed_results
