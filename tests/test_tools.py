"""Tests for the /vapi/tool endpoint."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.session_service import create_session, get_session


@pytest.fixture
async def client():
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def make_tool_request(call_id: str, tool_call_id: str, function_name: str, arguments: dict):
    """Helper to create a Vapi tool request payload."""
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "arguments": arguments,
                    },
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_check_duplicate_no_match(client):
    """Test check_duplicate when no patient exists."""
    create_session("test-call-1")
    payload = make_tool_request("test-call-1", "tc-1", "check_duplicate", {"phone_number": "9999999999"})
    response = await client.post("/vapi/tool", json=payload)
    assert response.status_code == 200
    data = response.json()
    result = json.loads(data["results"][0]["result"])
    assert result["duplicate"] is False


@pytest.mark.asyncio
async def test_save_patient_via_tool(client):
    """Test saving a patient through the tool endpoint."""
    create_session("test-call-2")
    payload = make_tool_request("test-call-2", "tc-2", "save_patient", {
        "first_name": "John",
        "last_name": "Smith",
        "date_of_birth": "06/15/1985",
        "sex": "Male",
        "phone_number": "5559876543",
        "address_line_1": "456 Oak Ave",
        "city": "Denver",
        "state": "CO",
        "zip_code": "80201",
    })
    response = await client.post("/vapi/tool", json=payload)
    assert response.status_code == 200
    data = response.json()
    result = json.loads(data["results"][0]["result"])
    assert result["result"] == "success"
    assert "patient_id" in result


@pytest.mark.asyncio
async def test_save_patient_idempotency(client):
    """Test that duplicate tool_call_id returns cached result."""
    create_session("test-call-3")
    payload = make_tool_request("test-call-3", "tc-3", "save_patient", {
        "first_name": "Alice",
        "last_name": "Johnson",
        "date_of_birth": "03/22/1992",
        "sex": "Female",
        "phone_number": "5551112222",
        "address_line_1": "789 Pine St",
        "city": "Portland",
        "state": "OR",
        "zip_code": "97201",
    })

    # First call
    response1 = await client.post("/vapi/tool", json=payload)
    assert response1.status_code == 200
    result1 = json.loads(response1.json()["results"][0]["result"])
    assert result1["result"] == "success"

    # Second call with same tool_call_id — should be idempotent
    response2 = await client.post("/vapi/tool", json=payload)
    assert response2.status_code == 200
    result2 = json.loads(response2.json()["results"][0]["result"])
    assert result2["result"] == "already_saved"
    assert result2["patient_id"] == result1["patient_id"]


@pytest.mark.asyncio
async def test_unknown_tool(client):
    """Test calling an unknown tool name."""
    create_session("test-call-4")
    payload = make_tool_request("test-call-4", "tc-4", "nonexistent_tool", {})
    response = await client.post("/vapi/tool", json=payload)
    assert response.status_code == 200
    data = response.json()
    result = json.loads(data["results"][0]["result"])
    assert "error" in result


@pytest.mark.asyncio
async def test_webhook_call_started(client):
    """Test call-started webhook creates session."""
    response = await client.post("/vapi/webhook", json={
        "message": {
            "type": "call-started",
            "call": {"id": "webhook-call-1"},
        }
    })
    assert response.status_code == 200
    session = get_session("webhook-call-1")
    assert session is not None


@pytest.mark.asyncio
async def test_webhook_call_ended(client):
    """Test call-ended webhook cleans up session."""
    create_session("webhook-call-2")
    response = await client.post("/vapi/webhook", json={
        "message": {
            "type": "call-ended",
            "call": {"id": "webhook-call-2"},
        }
    })
    assert response.status_code == 200
    session = get_session("webhook-call-2")
    assert session is None
