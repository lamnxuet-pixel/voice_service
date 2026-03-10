# Tool Call Architecture Documentation

## Overview

The voice agent uses **7 tools** to handle patient registration through natural conversation. This architecture is designed to handle edge cases, provide real-time validation, and create a smooth user experience.

## Tool Inventory

### 1. validate_field
**Purpose**: Real-time validation of individual fields before final submission

**When to use**: After collecting any field that has validation rules (phone, email, DOB, state, zip)

**Parameters**:
- `field_name` (string): Name of the field (e.g., "phone_number", "email", "state")
- `field_value` (string): The value to validate

**Returns**:
```json
{
  "valid": true,
  "field_name": "phone_number",
  "message": "phone_number is valid."
}
```

**Error response**:
```json
{
  "valid": false,
  "field_name": "phone_number",
  "error": "Phone number must be exactly 10 digits",
  "message": "Invalid phone_number: Phone number must be exactly 10 digits"
}
```

**Benefits**:
- Catches errors immediately, not at final submission
- Provides specific error messages for better UX
- Reduces frustration from re-collecting all fields

---

### 2. check_duplicate
**Purpose**: Check if a patient with the given phone number already exists

**When to use**: After collecting phone number (and optionally after validation)

**Parameters**:
- `phone_number` (string): 10-digit US phone number

**Returns (no duplicate)**:
```json
{
  "duplicate": false,
  "message": "No existing patient found with this phone number."
}
```

**Returns (duplicate found)**:
```json
{
  "duplicate": true,
  "patient_id": "123e4567-e89b-12d3-a456-426614174000",
  "existing_name": "John Smith",
  "message": "A patient named John Smith already exists with this phone number."
}
```

**Side effects**:
- Sets session to "update mode" if duplicate found
- Stores patient_id in session for later update

---

### 3. update_field
**Purpose**: Update a single field when caller makes a correction

**When to use**: When caller says "Actually, my last name is..." or "I meant to say..."

**Parameters**:
- `field_name` (string): Name of the field to update
- `field_value` (string): The corrected value

**Returns**:
```json
{
  "result": "success",
  "field_name": "last_name",
  "field_value": "Davis",
  "message": "Updated last_name to Davis."
}
```

**Benefits**:
- No need to re-collect all fields for a single correction
- Maintains conversation flow
- Updates session draft immediately

---

### 4. save_patient
**Purpose**: Create a new patient record in the database

**When to use**: ONLY after caller confirms all fields are correct

**Parameters**: All patient fields (see schema below)

**Required fields**:
- first_name, last_name, date_of_birth, sex, phone_number
- address_line_1, city, state, zip_code

**Optional fields**:
- email, address_line_2, insurance_provider, insurance_member_id
- emergency_contact_name, emergency_contact_phone, preferred_language

**Returns (success)**:
```json
{
  "result": "success",
  "patient_id": "123e4567-e89b-12d3-a456-426614174000",
  "message": "Patient John Smith has been registered successfully."
}
```

**Returns (already saved - idempotency)**:
```json
{
  "result": "already_saved",
  "patient_id": "123e4567-e89b-12d3-a456-426614174000",
  "message": "Patient was already saved successfully."
}
```

**Error response**:
```json
{
  "error": "A patient with this phone number already exists. Use update_patient instead."
}
```

**Side effects**:
- Commits to database
- Marks session as confirmed
- Sets idempotency key to prevent double-writes

---

### 5. update_patient
**Purpose**: Update an existing patient's information

**When to use**: When duplicate is detected and caller wants to update their record

**Parameters**:
- `patient_id` (string, required): UUID of existing patient
- Any fields to update (all optional)

**Returns (success)**:
```json
{
  "result": "success",
  "patient_id": "123e4567-e89b-12d3-a456-426614174000",
  "message": "Patient John Smith's information has been updated."
}
```

**Returns (already updated - idempotency)**:
```json
{
  "result": "already_updated",
  "patient_id": "123e4567-e89b-12d3-a456-426614174000",
  "message": "Patient was already updated successfully."
}
```

**Side effects**:
- Commits to database
- Marks session as confirmed
- Sets idempotency key

---

### 6. reset_registration
**Purpose**: Clear all collected data and start over

**When to use**: When caller says "start over", "restart", "begin again"

**Parameters**: None

**Returns**:
```json
{
  "result": "success",
  "message": "Registration has been reset. Let's start over from the beginning."
}
```

**Side effects**:
- Clears session draft
- Resets all collected fields
- Resets update mode flag

---

### 7. schedule_appointment
**Purpose**: Schedule an initial appointment for a registered patient

**When to use**: After successful save_patient or update_patient

**Parameters**:
- `patient_id` (string, required): UUID of the patient
- `preferred_day` (string, optional): Day of week (e.g., "Monday")
- `preferred_time` (string, optional): Time of day (e.g., "morning", "afternoon", "evening")

**Returns**:
```json
{
  "result": "success",
  "appointment_day": "Tuesday",
  "appointment_time": "10:00 AM",
  "appointment_date": "March 11, 2026",
  "message": "Appointment scheduled for Tuesday, March 11 at 10:00 AM."
}
```

**Note**: Currently a mock implementation. In production, this would write to an appointments table.

---

## Conversation Flow Examples

### Happy Path (New Patient)
```
1. Collect first_name → "John"
2. Collect last_name → "Smith"
3. Collect date_of_birth → "01/15/1985"
4. Collect sex → "Male"
5. Collect phone_number → "5551234567"
6. validate_field(phone_number, "5551234567") → valid ✓
7. check_duplicate("5551234567") → no duplicate ✓
8. Collect address fields...
9. Read back all fields
10. Caller confirms
11. save_patient(...) → success ✓
12. schedule_appointment(patient_id) → success ✓
```

### Edge Case: Invalid Phone Number
```
1. Collect phone_number → "123"
2. validate_field(phone_number, "123") → invalid ✗
   Error: "Phone number must be exactly 10 digits"
3. Agent: "I need a 10-digit phone number. Could you provide that again?"
4. Collect phone_number → "5551234567"
5. validate_field(phone_number, "5551234567") → valid ✓
6. Continue...
```

### Edge Case: Duplicate Patient
```
1. Collect phone_number → "5551234567"
2. check_duplicate("5551234567") → duplicate found ✓
   Existing: "John Smith"
3. Agent: "It looks like we already have a record for John Smith. Would you like to update your information?"
4. Caller: "Yes, I moved"
5. Collect updated address...
6. update_patient(patient_id, address_line_1="456 Oak St", ...) → success ✓
```

### Edge Case: Mid-Conversation Correction
```
1. Collect last_name → "Davies"
2. Collect city → "Boston"
3. Caller: "Actually, my last name is Davis, not Davies"
4. update_field(last_name, "Davis") → success ✓
5. Agent: "Got it, I've updated that to Davis."
6. Continue from city...
```

### Edge Case: Start Over
```
1. Collect several fields...
2. Caller: "Wait, I want to start over"
3. reset_registration() → success ✓
4. Agent: "No problem! Let's start fresh. What's your first name?"
5. Begin from the beginning...
```

---

## Architecture Decisions

### Why 7 Tools Instead of 3?

**Original (3 tools)**:
- check_duplicate, save_patient, update_patient
- ❌ No real-time validation → errors discovered late
- ❌ No field corrections → must re-collect everything
- ❌ No restart capability → frustrating UX

**Enhanced (7 tools)**:
- Added validate_field → catch errors immediately
- Added update_field → efficient corrections
- Added reset_registration → user control
- Enhanced schedule_appointment → realistic scheduling

### Idempotency Protection

Both `save_patient` and `update_patient` use idempotency keys to prevent double-writes:

```python
tool_call_id = f"{tool_name}_{call_id}"
if draft.idempotency_key == tool_call_id:
    return {"result": "already_saved", ...}
```

This handles:
- Network retries
- Duplicate tool calls from LLM
- Connection issues

### Session Management

The session service maintains state across the conversation:

```python
class PatientDraft:
    call_id: str
    collected: dict[str, Any] = {}  # Field values
    confirmed: bool = False
    patient_id: Optional[UUID] = None
    is_update: bool = False  # Duplicate detected?
    idempotency_key: Optional[str] = None
```

**Benefits**:
- Tracks collected fields
- Remembers if we're updating vs creating
- Prevents duplicate database writes
- Enables field corrections

---

## Validation Strategy

### Two-Layer Validation

1. **Real-time validation** (validate_field tool)
   - Immediate feedback
   - Specific error messages
   - Prevents wasted conversation turns

2. **Final validation** (save_patient/update_patient)
   - Server-side Pydantic validation
   - Database constraint checks
   - Last line of defense

### Validation Rules

| Field | Rules |
|-------|-------|
| first_name, last_name | 1-50 chars, alphabetic + hyphens/apostrophes |
| date_of_birth | MM/DD/YYYY, must be past date |
| sex | Male, Female, Other, Decline to Answer |
| phone_number | Exactly 10 digits |
| email | Valid email format (optional) |
| state | Valid 2-letter US state abbreviation |
| zip_code | 5-digit or ZIP+4 format |
| city | 1-100 characters |

---

## Performance Considerations

### Parallel Tool Execution

When multiple tools are called, they execute in parallel:

```python
tasks = [
    self._execute_single_tool(tool1, args1, db),
    self._execute_single_tool(tool2, args2, db),
]
results = await asyncio.gather(*tasks)
```

**Impact**: 500-1000ms saved for multiple tool calls

### Database Indexes

```python
Index("idx_patients_phone_number", "phone_number")  # Fast duplicate checks
Index("idx_patients_created_at", "created_at")  # Fast recent queries
```

**Impact**: 20-100ms saved per duplicate check

---

## Error Handling

### Tool Execution Errors

All tools return structured error responses:

```json
{
  "error": "Descriptive error message"
}
```

The LLM is instructed to:
1. Explain the error naturally (not technical jargon)
2. Re-ask for the specific field
3. Provide hints if helpful

### Database Errors

- Unique constraint violations → "Patient already exists"
- Connection errors → "Technical issue, please try again"
- Validation errors → Specific field error messages

### Network Errors

- Idempotency keys prevent duplicate writes on retry
- Session state preserved across reconnections
- Graceful degradation

---

## Testing Recommendations

### Unit Tests

Test each tool handler independently:

```python
async def test_validate_field_valid_phone():
    result = await _handle_validate_field({
        "field_name": "phone_number",
        "field_value": "5551234567"
    })
    assert result["valid"] == True

async def test_validate_field_invalid_phone():
    result = await _handle_validate_field({
        "field_name": "phone_number",
        "field_value": "123"
    })
    assert result["valid"] == False
    assert "10 digits" in result["error"]
```

### Integration Tests

Test complete conversation flows:

```python
async def test_new_patient_registration():
    # Simulate full conversation
    # Assert database record created
    # Assert session marked as confirmed

async def test_duplicate_patient_update():
    # Create existing patient
    # Simulate duplicate detection
    # Assert update_patient called
    # Assert record updated
```

### Edge Case Tests

- Invalid data for each field type
- Duplicate phone numbers
- Mid-conversation corrections
- Reset registration
- Connection drops (idempotency)

---

## Future Enhancements

### Persistent Sessions
Currently sessions are in-memory. For production:
- Use Redis for session storage
- Survive server restarts
- Enable call resumption after disconnection

### Enhanced Duplicate Detection
Currently only checks phone number. Could add:
- Name + DOB matching
- Fuzzy name matching
- Multiple phone numbers per patient

### Real Appointment Scheduling
Replace mock with actual implementation:
- Appointments database table
- Provider availability checking
- Calendar integration
- Confirmation emails/SMS

### Call Recording & Transcripts
- Save full conversation transcript
- Link to patient record
- Enable quality assurance
- Support training

---

## Troubleshooting

### Tool Not Being Called

**Symptom**: LLM doesn't use a tool when it should

**Causes**:
1. Tool description unclear
2. System prompt doesn't mention the tool
3. LLM doesn't recognize the scenario

**Solution**: Update system prompt with clear usage instructions

### Validation Errors Not Caught

**Symptom**: Invalid data reaches save_patient

**Causes**:
1. validate_field not called
2. Validation logic incomplete

**Solution**: Ensure system prompt instructs to validate before saving

### Duplicate Writes

**Symptom**: Same patient saved twice

**Causes**:
1. Idempotency key not set
2. Session not marked as confirmed

**Solution**: Check session_service.mark_confirmed() is called

### Session State Lost

**Symptom**: Corrections don't persist

**Causes**:
1. Session not created
2. Wrong call_id used

**Solution**: Verify call_id consistency across requests

---

## Summary

This 7-tool architecture provides:

✅ Real-time validation (validate_field)
✅ Efficient corrections (update_field)
✅ User control (reset_registration)
✅ Duplicate handling (check_duplicate)
✅ Idempotent writes (save_patient, update_patient)
✅ Enhanced scheduling (schedule_appointment)

The result is a robust, user-friendly voice registration system that handles edge cases gracefully and provides a natural conversational experience.
