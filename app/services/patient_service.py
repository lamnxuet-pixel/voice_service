"""Patient service — create, update, search, duplicate check logic."""

from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient, CallLog
from app.schemas.patient import PatientCreate, PatientUpdate

logger = structlog.get_logger()


async def create_patient(db: AsyncSession, data: PatientCreate) -> Patient:
    """Create a new patient record in the database."""
    patient = Patient(**data.model_dump())
    db.add(patient)
    await db.flush()
    await db.refresh(patient)
    logger.info("patient_created", patient_id=str(patient.id))
    return patient


async def update_patient(db: AsyncSession, patient_id: UUID, data: PatientUpdate) -> Optional[Patient]:
    """Update an existing patient record."""
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if patient is None:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(patient, field, value)

    await db.flush()
    await db.refresh(patient)
    logger.info("patient_updated", patient_id=str(patient.id), fields_updated=list(update_data.keys()))
    return patient


async def get_patient(db: AsyncSession, patient_id: UUID) -> Optional[Patient]:
    """Get a patient by ID."""
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    return result.scalar_one_or_none()


async def list_patients(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> list[Patient]:
    """List patients with pagination and optional filters."""
    query = select(Patient).where(Patient.deleted_at.is_(None))

    if last_name:
        query = query.where(Patient.last_name.ilike(f"%{last_name}%"))
    if date_of_birth:
        query = query.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        digits = re.sub(r"\D", "", phone_number)
        query = query.where(Patient.phone_number == digits)

    result = await db.execute(
        query.offset(skip).limit(limit).order_by(Patient.created_at.desc())
    )
    return list(result.scalars().all())


async def check_duplicate_by_phone(db: AsyncSession, phone_number: str) -> Optional[Patient]:
    """Check if a patient with this phone number already exists."""
    # Normalize to digits only
    import re
    digits = re.sub(r"\D", "", phone_number)

    result = await db.execute(
        select(Patient).where(
            Patient.phone_number == digits,
            Patient.deleted_at.is_(None),
        )
    )
    patient = result.scalar_one_or_none()
    if patient:
        logger.info("duplicate_found", phone_number_length=len(digits))
    return patient


async def soft_delete_patient(db: AsyncSession, patient_id: UUID) -> Optional[Patient]:
    """Soft-delete a patient by setting deleted_at."""
    from datetime import datetime

    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = result.scalar_one_or_none()
    if patient is None:
        return None

    patient.deleted_at = datetime.utcnow()
    await db.flush()
    logger.info("patient_soft_deleted", patient_id=str(patient.id))
    return patient


async def log_call(
    db: AsyncSession,
    call_id: str,
    patient_id: Optional[UUID],
    transcript: str,
    status: str = "completed",
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
) -> CallLog:
    """Save the call transcript to the database."""
    log_entry = CallLog(
        call_id=call_id,
        patient_id=patient_id,
        transcript=transcript,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
    )
    db.add(log_entry)
    await db.commit()
    return log_entry
