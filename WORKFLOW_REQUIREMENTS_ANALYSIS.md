# Workflow Requirements Analysis

## Current Workflow Coverage vs Requirements

### ✅ Fully Covered Requirements

1. **Error Handling** - Invalid data re-prompting
   - validate_field tool catches errors immediately
   - Fallback allows conversation to continue
   - ✅ COVERED

2. **Duplicate Detection** - Recognize returning callers
   - check_duplicate tool by phone number
   - Session marks as update mode
   - ✅ COVERED

3. **Database Write Failures** - Graceful error messages
   - save_patient returns error on failure
   - Fallback provides user-friendly message
   - ✅ COVERED

4. **Mid-conversation corrections** - Handle "Actually, it's..."
   - update_field tool for single corrections
   - ✅ COVERED

5. **Start over** - Caller wants to restart
   - reset_registration tool
   - ✅ COVERED

### ⚠️ Partially Covered Requirements

1. **Confirmation Before Saving** - Read back all fields
   - System prompt instructs to read back
   - ❌ NO TOOL to explicitly trigger confirmation flow
   - ❌ NO VALIDATION that confirmation happened

2. **Call Completion** - Graceful ending
   - System prompt mentions confirmation
   - ❌ NO TOOL to mark call as complete
   - ❌ NO CALL LOG saved during conversation

3. **Telephony Connection Drops** - Mid-call disconnection
   - Session is in-memory only
   - ❌ DATA LOST on disconnection
   - ❌ NO RESUME capability

4. **Observability** - Log conversations
   - Tool execution logged
   - ❌ CONVERSATION TRANSCRIPT not saved
   - ❌ NO LINK to patient record

### ❌ Missing Critical Features

1. **Confirmation Workflow**
   - No explicit confirmation state tracking
   - No tool to mark "ready for confirmation"
   - No validation that all required fields collected

2. **Call State Management**
   - No call status tracking (in_progress, completed, failed)
   - No call duration tracking
   - No call outcome tracking

3. **Transcript Storage**
   - Conversation not saved to database
   - No link between call_logs and conversation
   - No searchable transcript

4. **Resume Capability**
   - Session lost on disconnection
   - No way to resume partial registration
   - No persistent draft storage

5. **Field Collection Tracking**
   - No explicit tracking of which fields collected
   - No validation of required vs optional fields
   - No progress indicator

---

## Missing Edge Cases

### 1. Partial Registration Recovery

**Scenario**: Call drops after collecting 5 of 9 required fields

**Current Behavior**:
- Session lost (in-memory)
- User must start completely over
- Frustrating experience

**Required Behavior**:
- Session persisted to database/Redis
- User can resume from where they left off
- "Welcome back! Let's continue where we left off."

### 2. Confirmation Flow Validation

**Scenario**: LLM calls save_patient without reading back fields

**Current Behavior**:
- No validation that confirmation happened
- Data saved without user confirmation
- Violates requirement

**Required Behavior**:
- Track confirmation state
- Block save_patient until confirmed
- Force read-back before saving

### 3. Call Transcript Linking

**Scenario**: Need to review what was said during registration

**Current Behavior**:
- No transcript saved
- No link to patient record
- No audit trail

**Required Behavior**:
- Full transcript saved to call_logs
- Linked to patient_id
- Searchable for quality assurance

### 4. Multi-call Session Management

**Scenario**: User calls back to complete registration

**Current Behavior**:
- No way to identify incomplete registration
- Must start over
- Poor UX

**Required Behavior**:
- Detect incomplete registration by phone
- Offer to resume
- "I see you started registration earlier. Would you like to continue?"

### 5. Field Collection Progress

**Scenario**: Need to know which fields are still needed

**Current Behavior**:
- No explicit tracking
- LLM must remember from conversation
- Error-prone

**Required Behavior**:
- Track collected fields explicitly
- Validate required fields before confirmation
- "We still need your address and date of birth."

### 6. Call Outcome Tracking

**Scenario**: Need to know why call ended

**Current Behavior**:
- No outcome tracking
- Can't distinguish success vs failure vs abandonment

**Required Behavior**:
- Track call outcome (completed, abandoned, failed, error)
- Save to call_logs
- Enable analytics

---

## Proposed Enhancements

### 1. Add Persistent Session Storage

**Problem**: In-memory sessions lost on disconnection

**Solution**: Store sessions in database or Redis

```python
class PatientDraft(Base):
    __tablename__ = "patient_drafts"
    
    call_id = Column(String, primary_key=True)
    phone_number = Column(String, index=True)  # For resume detection
    collected = Column(JSON)  # Field values
    required_fields_collected = Column(JSON)  # Track progress
    confirmed = Column(Boolean, default=False)
    confirmation_timestamp = Column(DateTime, nullable=True)
    patient_id = Column(UUID, nullable=True)
    is_update = Column(Boolean, default=False)
    status = Column(String)  # collecting, confirming, saving, completed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    expires_at = Column(DateTime)  # Auto-cleanup after 24 hours
```

### 2. Add Confirmation Flow Tools

**New Tools**:

```python
# Mark ready for confirmation
confirm_ready(collected_fields: dict) -> dict:
    """
    Validate all required fields collected and mark ready for confirmation.
    Returns list of missing required fields if any.
    """
    
# Mark confirmation completed
confirm_completed() -> dict:
    """
    Mark that user has confirmed all fields are correct.
    Enables save_patient to proceed.
    """
    
# Get collection progress
get_progress() -> dict:
    """
    Return which fields collected, which still needed.
    Helps LLM know what to ask next.
    """
```

### 3. Add Call Management Tools

**New Tools**:

```python
# Start call tracking
start_call(phone_number: str) -> dict:
    """
    Initialize call tracking, check for incomplete registrations.
    Returns resume option if found.
    """
    
# End call tracking
end_call(outcome: str, summary: str) -> dict:
    """
    Mark call as complete, save transcript, update call_logs.
    Outcomes: completed, abandoned, failed, error
    """
    
# Save conversation turn
save_turn(speaker: str, message: str) -> dict:
    """
    Save each conversation turn for transcript building.
    """
```

### 4. Add Resume Detection

**New Tool**:

```python
# Check for incomplete registration
check_incomplete_registration(phone_number: str) -> dict:
    """
    Check if this phone number has an incomplete registration.
    Returns draft data if found.
    """
```

### 5. Enhanced save_patient Validation

**Add Pre-save Checks**:

```python
async def _handle_save_patient(self, arguments: dict) -> dict:
    draft = session_service.get_or_create_session(self.call_id)
    
    # NEW: Check confirmation happened
    if not draft.confirmed:
        return {
            "result": "error",
            "error": "not_confirmed",
            "message": "Please confirm all information is correct before saving.",
        }
    
    # NEW: Validate all required fields present
    required = ["first_name", "last_name", "date_of_birth", "sex", 
                "phone_number", "address_line_1", "city", "state", "zip_code"]
    missing = [f for f in required if f not in arguments or not arguments[f]]
    
    if missing:
        return {
            "result": "error",
            "error": "missing_required_fields",
            "missing_fields": missing,
            "message": f"Missing required fields: {', '.join(missing)}",
        }
    
    # Continue with save...
```

---

## Complete Workflow Architecture

### Enhanced Tool Set (14 tools)

**Existing (7)**:
1. validate_field
2. check_duplicate
3. update_field
4. save_patient
5. update_patient
6. reset_registration
7. schedule_appointment

**New (7)**:
8. start_call - Initialize call tracking
9. check_incomplete_registration - Resume detection
10. get_progress - Field collection status
11. confirm_ready - Validate ready for confirmation
12. confirm_completed - Mark confirmation done
13. save_turn - Save conversation transcript
14. end_call - Complete call tracking

### Enhanced Workflow Flow

```
1. Call Starts
   ├─ start_call(phone_number)
   │  ├─ Create call_log entry (status: in_progress)
   │  ├─ Check for incomplete registration
   │  └─ Return resume option if found
   │
2. Field Collection Phase
   ├─ For each field:
   │  ├─ Collect from user
   │  ├─ validate_field(field_name, value)
   │  ├─ update_field(field_name, value) if valid
   │  └─ save_turn(speaker, message) for transcript
   │
   ├─ check_duplicate(phone_number) after phone collected
   │
   └─ get_progress() to check what's still needed
   │
3. Confirmation Phase
   ├─ confirm_ready(collected_fields)
   │  ├─ Validates all required fields present
   │  ├─ Returns missing fields if any
   │  └─ Marks status: confirming
   │
   ├─ Read back all fields to user
   │
   ├─ User confirms or corrects
   │  └─ If corrections: update_field() and re-confirm
   │
   └─ confirm_completed()
      └─ Marks confirmed: true
   │
4. Save Phase
   ├─ save_patient(all_fields) or update_patient(patient_id, fields)
   │  ├─ Validates confirmation happened
   │  ├─ Validates required fields present
   │  ├─ Commits to database
   │  └─ Marks status: completed
   │
   └─ schedule_appointment(patient_id) [optional]
   │
5. Call End
   └─ end_call(outcome: "completed", summary: "...")
      ├─ Saves full transcript to call_logs
      ├─ Links to patient_id
      ├─ Updates call status
      └─ Cleans up session
```

### State Machine

```
┌─────────────┐
│   started   │
└──────┬──────┘
       │ start_call()
       ▼
┌─────────────┐
│ collecting  │◄──┐
└──────┬──────┘   │
       │          │ update_field()
       │ confirm_ready()
       ▼          │
┌─────────────┐   │
│ confirming  │───┘
└──────┬──────┘
       │ confirm_completed()
       ▼
┌─────────────┐
│   saving    │
└──────┬──────┘
       │ save_patient()
       ▼
┌─────────────┐
│  completed  │
└──────┬──────┘
       │ end_call()
       ▼
┌─────────────┐
│    ended    │
└─────────────┘
```

---

## Implementation Priority

### P0 - Critical (Blocks Requirements)

1. **Confirmation Flow Validation**
   - Add confirm_ready() tool
   - Add confirm_completed() tool
   - Block save_patient until confirmed
   - **Impact**: Satisfies "confirmation before saving" requirement

2. **Call Transcript Storage**
   - Add save_turn() tool
   - Save to call_logs.transcript
   - Link to patient_id
   - **Impact**: Satisfies "observability" requirement

3. **Call State Tracking**
   - Add start_call() tool
   - Add end_call() tool
   - Track call status and outcome
   - **Impact**: Satisfies "call completion" requirement

### P1 - High (Improves Resilience)

4. **Persistent Session Storage**
   - Move sessions to database/Redis
   - Add expiration (24 hours)
   - **Impact**: Handles "connection drops" edge case

5. **Resume Capability**
   - Add check_incomplete_registration() tool
   - Detect and offer resume
   - **Impact**: Improves UX for dropped calls

6. **Field Collection Tracking**
   - Add get_progress() tool
   - Track required vs optional fields
   - **Impact**: Helps LLM know what to ask

### P2 - Medium (Nice to Have)

7. **Enhanced Duplicate Detection**
   - Check name + DOB, not just phone
   - Fuzzy matching
   - **Impact**: Better duplicate detection

8. **Call Analytics**
   - Track call duration
   - Track field collection time
   - Track retry counts
   - **Impact**: Better monitoring

---

## Recommended Next Steps

1. **Implement P0 items** (Confirmation + Transcript + Call State)
2. **Update system prompt** to use new tools
3. **Add validation** to save_patient
4. **Write tests** for new workflows
5. **Deploy to staging** for testing
6. **Implement P1 items** (Persistent sessions + Resume)
7. **Production deployment**

---

## Summary

**Current Workflow**: 7 tools, handles basic cases
**Complete Workflow**: 14 tools, handles all requirements and edge cases

**Key Gaps**:
- ❌ No confirmation flow validation
- ❌ No transcript storage
- ❌ No call state tracking
- ❌ No resume capability
- ❌ No persistent sessions

**Impact of Gaps**:
- Violates "confirmation before saving" requirement
- Violates "observability" requirement
- Poor handling of "connection drops" edge case
- Incomplete "call completion" tracking

**Recommendation**: Implement P0 items immediately to satisfy core requirements.
