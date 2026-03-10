"""Seed data — insert demo patients on first run if table is empty."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient

logger = structlog.get_logger()

SEED_PATIENTS = [
    {
        "id": uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890"),
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "01/15/1990",
        "sex": "Female",
        "phone_number": "5551234567",
        "email": "jane.doe@example.com",
        "address_line_1": "123 Main St",
        "address_line_2": "Apt 4B",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62701",
        "insurance_provider": "Blue Cross",
        "insurance_member_id": "BCX-98765",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe",
        "emergency_contact_phone": "5559876543",
    },
    {
        "id": uuid.UUID("b2c3d4e5-f6a7-8901-bcde-f12345678901"),
        "first_name": "Carlos",
        "last_name": "Rivera",
        "date_of_birth": "03/22/1985",
        "sex": "Male",
        "phone_number": "5559871234",
        "email": None,
        "address_line_1": "456 Oak Ave",
        "address_line_2": None,
        "city": "Denver",
        "state": "CO",
        "zip_code": "80201",
        "insurance_provider": None,
        "insurance_member_id": None,
        "preferred_language": "Spanish",
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
    },
]


async def seed_patients(db: AsyncSession) -> None:
    """Insert seed patients if the table is empty."""
    result = await db.execute(select(func.count()).select_from(Patient))
    count = result.scalar() or 0

    if count > 0:
        logger.info("seed_skipped", existing_count=count)
        return

    now = datetime.utcnow()
    for data in SEED_PATIENTS:
        patient = Patient(**data, created_at=now, updated_at=now)
        db.add(patient)

    await db.commit()
    logger.info("seed_completed", patients_seeded=len(SEED_PATIENTS))
