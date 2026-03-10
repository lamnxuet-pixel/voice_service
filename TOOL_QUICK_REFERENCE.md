# Tool Call Quick Reference

## Tool Execution Order

```
1. validate_field     → Check if data is valid
2. check_duplicate    → Check if patient exists
3. update_field       → Correct a single field
4. save_patient       → Create new patient
5. update_patient     → Update existing patient
6. schedule_appointment → Book appointment
7. reset_registration → Start over
```

## Tool Cheat Sheet

### validate_field
```json
// Request
{
  "field_name": "phone_number",
  "field_value": "5551234567"
}

// Response (valid)
{
  "valid": true,
  "field_name": "phone_number",
  "message": "phone_number is valid."
}

// Response (invalid)
{
  "valid": false,
  "field_name": "phone_number",
  "error": "Phone number must be exactly 10 digits",
  "message": "Invalid phone_number: Phone number must be exactly 10 digits"
}
```

**When**: After collecting any validated field
**Validates**: phone_number, email, date_of_birth, state, zip_code, names

---

### check_duplicate
```json
// Request
{
  "phone_number": "5551234567"
}

// Response (no duplicate)
{
  "duplicate": false,
  "message": "No existing patient found with this phone number."
}

// Response (duplicate found)
{
  "duplicate": true,
  "patient_id": "uuid-here",
  "existing_name": "John Smith",
  "message": "A patient named John Smith already exists with this phone number."
}
```

**When**: After collecting phone number
**Side effect**: Sets session to update mode if duplicate found

---

### update_field
```json
// Request
{
  "field_name": "last_name",
  "field_value": "Davis"
}

// Response
{
  "result": "success",
  "field_name": "last_name",
  "field_value": "Davis",
  "message": "Updated last_name to Davis."
}
```

**When**: User makes a correction ("Actually, it's...")
**Side effect**: Updates session draft

---

### save_patient
```json
// Request (required fields only)
{
  "first_name": "John",
  "last_name": "Smith",
  "date_of_birth": "01/15/1985",
  "sex": "Male",
  "phone_number": "5551234567",
  "address_line_1": "123 Main St",
  "city": "Boston",
  "state": "MA",
  "zip_code": "02101"
}

// Response (success)
{
  "result": "success",
  "patient_id": "uuid-here",
  "message": "Patient John Smith has been registered successfully."
}

// Response (idempotent)
{
  "result": "already_saved",
  "patient_id": "uuid-here",
  "message": "Patient was already saved successfully."
}
```

**When**: ONLY after user confirms all fields
**Side effect**: Commits to database, marks session confirmed

---

### update_patient
```json
// Request
{
  "patient_id": "uuid-here",
  "address_line_1": "456 Oak St",
  "city": "Cambridge"
}

// Response (success)
{
  "result": "success",
  "patient_id": "uuid-here",
  "message": "Patient John Smith's information has been updated."
}
```

**When**: Duplicate detected and user wants to update
**Side effect**: Commits to database

---

### reset_registration
```json
// Request
{}

// Response
{
  "result": "success",
  "message": "Registration has been reset. Let's start over from the beginning."
}
```

**When**: User says "start over", "restart", "begin again"
**Side effect**: Clears session draft completely

---

### schedule_appointment
```json
// Request (minimal)
{
  "patient_id": "uuid-here"
}

// Request (with preferences)
{
  "patient_id": "uuid-here",
  "preferred_day": "Monday",
  "preferred_time": "morning"
}

// Response
{
  "result": "success",
  "appointment_day": "Monday",
  "appointment_time": "9:00 AM",
  "appointment_date": "March 10, 2026",
  "message": "Appointment scheduled for Monday, March 10 at 9:00 AM."
}
```

**When**: After successful save_patient or update_patient
**Note**: Currently mock implementation

---

## Common Patterns

### Pattern 1: New Patient (Happy Path)
```
1. Collect fields
2. validate_field for each validated field
3. check_duplicate(phone_number)
4. Read back all fields
5. save_patient(all_fields)
6. schedule_appointment(patient_id)
```

### Pattern 2: Invalid Data
```
1. Collect field
2. validate_field → invalid
3. Re-ask for that specific field
4. validate_field → valid
5. Continue
```

### Pattern 3: Correction
```
1. Collecting fields...
2. User: "Actually, my last name is..."
3. update_field(last_name, corrected_value)
4. Continue from where we were
```

### Pattern 4: Duplicate Patient
```
1. check_duplicate → duplicate found
2. Ask if user wants to update
3. Collect updated fields
4. update_patient(patient_id, updated_fields)
5. schedule_appointment(patient_id)
```

### Pattern 5: Start Over
```
1. User: "I want to start over"
2. reset_registration()
3. Begin from first_name
```

---

## Validation Rules

| Field | Rule | Example Valid | Example Invalid |
|-------|------|---------------|-----------------|
| first_name | 1-50 chars, alphabetic | "John" | "J0hn" |
| last_name | 1-50 chars, alphabetic | "O'Brien" | "Smith123" |
| date_of_birth | MM/DD/YYYY, past | "01/15/1985" | "15/01/1985" |
| sex | Enum | "Male" | "M" |
| phone_number | 10 digits | "5551234567" | "123" |
| email | Valid email | "john@example.com" | "john@" |
| state | 2-letter US | "MA" | "Mass" |
| zip_code | 5 or 9 digits | "02101" | "123" |
| city | 1-100 chars | "Boston" | "" |

---

## Error Handling

### Tool Returns Error
```json
{
  "error": "Descriptive error message"
}
```

**LLM should**:
1. Explain error naturally (no technical jargon)
2. Re-ask for the specific field
3. Provide helpful hints

### Database Error
```json
{
  "error": "A patient with this phone number already exists. Use update_patient instead."
}
```

**LLM should**:
1. Acknowledge the issue
2. Offer to update instead
3. Continue conversation flow

---

## Session State

### Session Fields
```python
{
  "call_id": "abc123",
  "collected": {
    "first_name": "John",
    "last_name": "Smith",
    # ... other fields
  },
  "confirmed": false,
  "patient_id": null,
  "is_update": false,
  "idempotency_key": null
}
```

### State Transitions
```
Initial → Collecting → Confirming → Saved
                    ↓
                 Updating (if duplicate)
                    ↓
                 Saved
```

---

## Testing Commands

### Test validate_field
```bash
curl -X POST http://localhost:8000/vapi/tool \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "toolCallList": [{
        "id": "test1",
        "function": {
          "name": "validate_field",
          "arguments": {
            "field_name": "phone_number",
            "field_value": "5551234567"
          }
        }
      }],
      "call": {"id": "test-call"}
    }
  }'
```

### Test update_field
```bash
curl -X POST http://localhost:8000/vapi/tool \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "toolCallList": [{
        "id": "test2",
        "function": {
          "name": "update_field",
          "arguments": {
            "field_name": "last_name",
            "field_value": "Davis"
          }
        }
      }],
      "call": {"id": "test-call"}
    }
  }'
```

### Test reset_registration
```bash
curl -X POST http://localhost:8000/vapi/tool \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "toolCallList": [{
        "id": "test3",
        "function": {
          "name": "reset_registration",
          "arguments": {}
        }
      }],
      "call": {"id": "test-call"}
    }
  }'
```

---

## Debugging Tips

### Tool Not Called
- Check system prompt mentions the tool
- Verify tool description is clear
- Check LLM recognizes the scenario

### Validation Not Working
- Verify field_name matches schema
- Check Pydantic validators are correct
- Test with curl command

### Session State Lost
- Verify call_id is consistent
- Check session_service is working
- Look for session creation logs

### Idempotency Issues
- Check idempotency_key is set
- Verify mark_confirmed() is called
- Look for "already_saved" responses

---

## Performance Notes

- validate_field: ~10ms (Pydantic validation)
- update_field: ~1ms (in-memory)
- reset_registration: ~1ms (in-memory)
- check_duplicate: ~20-50ms (DB query with index)
- save_patient: ~50-100ms (DB write + commit)
- update_patient: ~50-100ms (DB write + commit)
- schedule_appointment: ~1ms (mock)

**Total typical call**: ~2-3 seconds (including LLM time)

---

## Monitoring

### Key Metrics to Track
- Tool call frequency (which tools are used most)
- Validation failure rate (how often validate_field returns invalid)
- Correction rate (how often update_field is called)
- Reset rate (how often reset_registration is called)
- Duplicate rate (how often check_duplicate finds matches)

### Log Messages to Watch
```
tool_execution_start
tool_execution_error
validation_failed
session_reset
duplicate_found
patient_created
patient_updated
```

---

## Quick Troubleshooting

| Problem | Check | Solution |
|---------|-------|----------|
| Tool not called | System prompt | Add tool usage instructions |
| Validation fails | Field name | Match exact schema field names |
| Session lost | call_id | Ensure consistency across requests |
| Duplicate write | Idempotency | Check mark_confirmed() called |
| Error not handled | Error response | Return structured error dict |

---

## Resources

- **Full Documentation**: TOOL_ARCHITECTURE.md
- **Implementation Details**: TOOL_IMPROVEMENTS_SUMMARY.md
- **API Reference**: API_REFERENCE.md
- **Code**: app/prompts/patient_registration.py
