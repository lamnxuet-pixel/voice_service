"""Tests for the /patients REST API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


VALID_PATIENT = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "01/15/1990",
    "sex": "Female",
    "phone_number": "5551234567",
    "address_line_1": "123 Main St",
    "city": "Springfield",
    "state": "IL",
    "zip_code": "62701",
}


@pytest.mark.asyncio
async def test_health(client):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_root(client):
    """Test root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Voice Patient Registration"


@pytest.mark.asyncio
async def test_create_patient(client):
    """Test creating a new patient."""
    response = await client.post("/patients/", json=VALID_PATIENT)
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "Doe"
    assert data["phone_number"] == "5551234567"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_patient_invalid_dob(client):
    """Test creating a patient with invalid DOB."""
    bad = {**VALID_PATIENT, "date_of_birth": "13/40/1990"}
    response = await client.post("/patients/", json=bad)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_patient_future_dob(client):
    """Test creating a patient with future DOB."""
    bad = {**VALID_PATIENT, "date_of_birth": "01/01/2099"}
    response = await client.post("/patients/", json=bad)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_patient_invalid_phone(client):
    """Test creating a patient with invalid phone number."""
    bad = {**VALID_PATIENT, "phone_number": "123"}
    response = await client.post("/patients/", json=bad)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_patient_invalid_state(client):
    """Test creating a patient with invalid state."""
    bad = {**VALID_PATIENT, "state": "XX"}
    response = await client.post("/patients/", json=bad)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_patients(client):
    """Test listing patients."""
    response = await client.get("/patients/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
