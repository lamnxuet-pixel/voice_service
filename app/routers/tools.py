"""Tool execution endpoint — Vapi calls this when Gemini emits a tool_call."""

from __future__ import annotations

import json
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.patient import PatientCreate, PatientUpdate
from app.services import patient_service, session_service

logger = structlog.get_logger()

router = APIRouter(tags=["tools"])


async def _handle_validate_field(arguments: dict) -> dict:
    """Validate a single field in real-time."""
    from app.schemas.patient import PatientCreate, PatientUpdate
    
    field_name = arguments.get("field_name", "")
    field_value = arguments.get("field_value", "")
    
    if not field_name or not field_value:
        return {"error": "field_name and field_value are required"}
    
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


async def _handle_update_field(arguments: dict, call_id: str) -> dict:
    """Update a single field in the current registration draft."""
    field_name = arguments.get("field_name", "")
    field_value = arguments.get("field_value", "")
    
    if not field_name:
        return {"error": "field_name is required"}
    
    draft = session_service.get_or_create_session(call_id)
    draft.collected[field_name] = field_value
    
    return {
        "result": "success",
        "field_name": field_name,
        "field_value": field_value,
        "message": f"Updated {field_name} to {field_value}.",
    }


async def _handle_reset_registration(call_id: str) -> dict:
    """Reset the registration session and start over."""
    session_service.reset_session(call_id)
    
    return {
        "result": "success",
        "message": "Registration has been reset. Let's start over from the beginning.",
    }


@router.post("/vapi/tool")
async def tool_handler(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Vapi tool execution endpoint with unified executor.

    When Gemini emits a function call, Vapi sends the tool call here.
    Uses unified executor that toggles between standard and advanced workflows.
    """
    from app.services.tool_executor import execute_tools
    
    body = await request.json()
    message = body.get("message", {})

    # Extract tool call details
    tool_calls = message.get("toolCallList", [])
    call_info = message.get("call", {})
    call_id = call_info.get("id", "unknown")

    logger.info("tool_request_received", call_id=call_id, tool_count=len(tool_calls))

    # Prepare tool calls for execution
    batch_calls = []
    tool_call_ids = []
    
    for tool_call in tool_calls:
        tool_call_id = tool_call.get("id", "")
        function_info = tool_call.get("function", {})
        function_name = function_info.get("name", "")
        arguments = function_info.get("arguments", {})

        # Parse arguments if they're a string
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        batch_calls.append({
            "name": function_name,
            "arguments": arguments,
        })
        tool_call_ids.append(tool_call_id)

    # Execute using unified executor (auto-selects workflow mode)
    results = await execute_tools(
        call_id=call_id,
        db=db,
        tool_calls=batch_calls,
        timeout=5.0,
    )

    # Format results for Vapi
    formatted_results = []
    for tool_call_id, result in zip(tool_call_ids, results):
        formatted_results.append({
            "toolCallId": tool_call_id,
            "result": json.dumps(result) if isinstance(result, dict) else str(result),
        })

    return {"results": formatted_results}


async def _execute_tool(
    function_name: str,
    arguments: dict,
    call_id: str,
    tool_call_id: str,
    db: AsyncSession,
) -> dict:
    """Route and execute a tool call."""

    if function_name == "validate_field":
        return await _handle_validate_field(arguments)

    elif function_name == "update_field":
        return await _handle_update_field(arguments, call_id)

    elif function_name == "reset_registration":
        return await _handle_reset_registration(call_id)

    elif function_name == "check_duplicate":
        return await _handle_check_duplicate(arguments, call_id, db)

    elif function_name == "save_patient":
        return await _handle_save_patient(arguments, call_id, tool_call_id, db)

    elif function_name == "update_patient":
        return await _handle_update_patient(arguments, call_id, tool_call_id, db)

    elif function_name == "schedule_appointment":
        return await _handle_schedule_appointment(arguments)

    else:
        return {"error": f"Unknown tool: {function_name}"}


async def _handle_check_duplicate(
    arguments: dict,
    call_id: str,
    db: AsyncSession,
) -> dict:
    """Check if a patient with this phone number already exists."""
    phone_number = arguments.get("phone_number", "")
    if not phone_number:
        return {"error": "phone_number is required"}

    existing = await patient_service.check_duplicate_by_phone(db, phone_number)
    if existing:
        # Mark session as update mode
        draft = session_service.get_or_create_session(call_id)
        draft.is_update = True
        draft.patient_id = existing.id
        return {
            "duplicate": True,
            "patient_id": str(existing.id),
            "existing_name": f"{existing.first_name} {existing.last_name}",
            "message": f"A patient named {existing.first_name} {existing.last_name} already exists with this phone number.",
        }
    return {"duplicate": False, "message": "No existing patient found with this phone number."}


async def _handle_save_patient(
    arguments: dict,
    call_id: str,
    tool_call_id: str,
    db: AsyncSession,
) -> dict:
    """Save a new patient — with idempotency check."""
    draft = session_service.get_or_create_session(call_id)

    # Idempotency: prevent double-write on Vapi retry
    if draft.idempotency_key == tool_call_id:
        logger.info("idempotent_save_skipped", call_id=call_id, tool_call_id=tool_call_id)
        return {
            "result": "already_saved",
            "patient_id": str(draft.patient_id),
            "message": "Patient was already saved successfully.",
        }

    # Validate and create
    try:
        patient_data = PatientCreate(**arguments)
    except Exception as e:
        return {"error": str(e)}

    try:
        patient = await patient_service.create_patient(db, patient_data)
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "patients_phone_unique" in str(e):
            return {"error": "A patient with this phone number already exists. Use update_patient instead."}
        return {"error": f"Failed to save patient: {str(e)}"}

    # Mark session as confirmed
    session_service.mark_confirmed(call_id, patient.id, tool_call_id)

    return {
        "result": "success",
        "patient_id": str(patient.id),
        "message": f"Patient {patient.first_name} {patient.last_name} has been registered successfully.",
    }


async def _handle_update_patient(
    arguments: dict,
    call_id: str,
    tool_call_id: str,
    db: AsyncSession,
) -> dict:
    """Update an existing patient — with idempotency check."""
    draft = session_service.get_or_create_session(call_id)

    # Idempotency check
    if draft.idempotency_key == tool_call_id:
        logger.info("idempotent_update_skipped", call_id=call_id, tool_call_id=tool_call_id)
        return {
            "result": "already_updated",
            "patient_id": str(draft.patient_id),
            "message": "Patient was already updated successfully.",
        }

    patient_id_str = arguments.pop("patient_id", None)
    if not patient_id_str:
        return {"error": "patient_id is required"}

    try:
        patient_id = UUID(patient_id_str)
    except ValueError:
        return {"error": "Invalid patient_id format"}

    # Validate update data
    try:
        update_data = PatientUpdate(**arguments)
    except Exception as e:
        return {"error": str(e)}

    try:
        patient = await patient_service.update_patient(db, patient_id, update_data)
        await db.commit()
    except Exception as e:
        await db.rollback()
        return {"error": f"Failed to update patient: {str(e)}"}

    if patient is None:
        return {"error": "Patient not found"}

    # Mark session
    session_service.mark_confirmed(call_id, patient.id, tool_call_id)

    return {
        "result": "success",
        "patient_id": str(patient.id),
        "message": f"Patient {patient.first_name} {patient.last_name}'s information has been updated.",
    }


async def _handle_schedule_appointment(arguments: dict) -> dict:
    """Enhanced appointment scheduling with preferences."""
    import random
    from datetime import datetime, timedelta
    
    patient_id_str = arguments.get("patient_id")
    if not patient_id_str:
        return {"error": "patient_id is required to schedule an appointment"}
    
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
    
    logger.info("appointment_scheduled", patient_id=patient_id_str, day=day, time=time)
    
    return {
        "result": "success",
        "appointment_day": day,
        "appointment_time": time,
        "appointment_date": appointment_date.strftime("%B %d, %Y"),
        "message": f"Appointment scheduled for {day}, {appointment_date.strftime('%B %d')} at {time}.",
    }
