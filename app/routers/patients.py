"""Patient CRUD REST API router."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.patient import PatientCreate, PatientOut, PatientUpdate
from app.services import patient_service

logger = structlog.get_logger()

router = APIRouter(prefix="/patients", tags=["patients"])


def _envelope(data=None, error=None):
    """Consistent JSON response envelope."""
    return {"data": data, "error": error}


@router.post("/", status_code=201)
async def create_patient(
    data: PatientCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new patient record."""
    try:
        patient = await patient_service.create_patient(db, data)
        return _envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))
    except Exception as e:
        if "patients_phone_unique" in str(e):
            raise HTTPException(status_code=409, detail="A patient with this phone number already exists")
        raise


@router.get("/")
async def list_patients(
    skip: int = 0,
    limit: int = 50,
    last_name: Optional[str] = Query(None, description="Filter by last name"),
    date_of_birth: Optional[str] = Query(None, description="Filter by DOB (MM/DD/YYYY)"),
    phone_number: Optional[str] = Query(None, description="Filter by phone number"),
    db: AsyncSession = Depends(get_db),
):
    """List all patients with optional query filters."""
    patients = await patient_service.list_patients(
        db,
        skip=skip,
        limit=limit,
        last_name=last_name,
        date_of_birth=date_of_birth,
        phone_number=phone_number,
    )
    return _envelope(data=[PatientOut.model_validate(p).model_dump(mode="json") for p in patients])


@router.get("/{patient_id}")
async def get_patient(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a patient by ID."""
    patient = await patient_service.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))


@router.put("/{patient_id}")
async def update_patient(
    patient_id: UUID,
    data: PatientUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update an existing patient record. Partial updates allowed."""
    patient = await patient_service.update_patient(db, patient_id, data)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))


@router.delete("/{patient_id}", status_code=200)
async def delete_patient(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a patient (sets deleted_at timestamp)."""
    patient = await patient_service.soft_delete_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _envelope(data={"message": "Patient soft-deleted", "patient_id": str(patient_id)})


@router.get("/search/phone/{phone_number}")
async def search_by_phone(
    phone_number: str,
    db: AsyncSession = Depends(get_db),
):
    """Search for a patient by phone number."""
    patient = await patient_service.check_duplicate_by_phone(db, phone_number)
    if patient is None:
        raise HTTPException(status_code=404, detail="No patient found with this phone number")
    return _envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))
