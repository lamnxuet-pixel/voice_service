"""In-process session store keyed by Vapi call_id."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


class PatientDraft(BaseModel):
    call_id: str
    collected: dict[str, Any] = {}
    confirmed: bool = False
    patient_id: Optional[UUID] = None
    is_update: bool = False
    idempotency_key: Optional[str] = None


# In-process session store
_session_store: dict[str, PatientDraft] = {}


def create_session(call_id: str) -> PatientDraft:
    """Create a new session draft for a call."""
    draft = PatientDraft(call_id=call_id)
    _session_store[call_id] = draft
    logger.info("session_created", call_id=call_id)
    return draft


def get_session(call_id: str) -> Optional[PatientDraft]:
    """Retrieve a session draft by call_id."""
    return _session_store.get(call_id)


def get_or_create_session(call_id: str) -> PatientDraft:
    """Get existing session or create a new one."""
    draft = _session_store.get(call_id)
    if draft is None:
        draft = create_session(call_id)
    return draft


def update_session(call_id: str, **fields: Any) -> Optional[PatientDraft]:
    """Update fields in a session draft."""
    draft = _session_store.get(call_id)
    if draft is None:
        return None
    draft.collected.update(fields)
    logger.info("session_updated", call_id=call_id, fields_updated=list(fields.keys()))
    return draft


def reset_session(call_id: str) -> PatientDraft:
    """Reset a session draft (caller says 'start over')."""
    draft = PatientDraft(call_id=call_id)
    _session_store[call_id] = draft
    logger.info("session_reset", call_id=call_id)
    return draft


def delete_session(call_id: str) -> None:
    """Remove a session draft after call ends."""
    _session_store.pop(call_id, None)
    logger.info("session_deleted", call_id=call_id)


def mark_confirmed(call_id: str, patient_id: UUID, idempotency_key: str) -> Optional[PatientDraft]:
    """Mark a session as confirmed after successful DB write."""
    draft = _session_store.get(call_id)
    if draft is None:
        return None
    draft.confirmed = True
    draft.patient_id = patient_id
    draft.idempotency_key = idempotency_key
    logger.info("session_confirmed", call_id=call_id, patient_id=str(patient_id))
    return draft
