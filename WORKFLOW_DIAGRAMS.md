# LLM Tool Use Workflow Diagrams

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER (Caller)                            │
└────────────────────────────┬────────────────────────────────────┘
                             │ Voice
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PIPECAT VOICE PIPELINE                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Deepgram    │→ │   Gemini     │→ │  Deepgram    │         │
│  │     STT      │  │     LLM      │  │     TTS      │         │
│  └──────────────┘  └──────┬───────┘  └──────────────┘         │
└────────────────────────────┼────────────────────────────────────┘
                             │ Tool Calls
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TOOL WORKFLOW SYSTEM                          │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  ToolWorkflow (with caching & fallbacks)                 │  │
│  └──────────────────────────┬───────────────────────────────┘  │
└─────────────────────────────┼──────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    BACKEND SERVICES                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Session    │  │   Patient    │  │   Database   │         │
│  │   Service    │  │   Service    │  │  PostgreSQL  │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Complete Call Flow (Step-by-Step)

```
PHASE 1: CALL INITIATION
═══════════════════════════════════════════════════════════════

User calls → Daily.co WebRTC → Pipecat Bot starts

┌─────────────────────────────────────────────────────────────┐
│ 1. User: "Hi, I'd like to register"                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (STT)
┌─────────────────────────────────────────────────────────────┐
│ 2. Gemini LLM receives: "Hi, I'd like to register"          │
│    System Prompt: "You are Alex, a patient coordinator..."  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (LLM generates response)
┌─────────────────────────────────────────────────────────────┐
│ 3. Gemini: "Hello! I'd be happy to help. What's your       │
│            phone number?"                                    │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (TTS)
┌─────────────────────────────────────────────────────────────┐
│ 4. User hears: "Hello! I'd be happy to help..."            │
└─────────────────────────────────────────────────────────────┘


PHASE 2: FIELD COLLECTION WITH TOOLS
═══════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────┐
│ 5. User: "555-123-4567"                                     │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (STT)
┌─────────────────────────────────────────────────────────────┐
│ 6. Gemini receives: "555-123-4567"                          │
│    Decides to call tools:                                    │
│    - start_call(phone_number="5551234567")                  │
│    - validate_field(field_name="phone_number",              │
│                     field_value="5551234567")               │
│    - update_field(field_name="phone_number",                │
│                   field_value="5551234567")                 │
│    - check_duplicate(phone_number="5551234567")             │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (Tool execution)
┌─────────────────────────────────────────────────────────────┐
│ 7. ToolWorkflow.execute_tools_batch([...])                  │
│    ┌─────────────────────────────────────────────────────┐ │
│    │ PARALLEL EXECUTION (with caching)                   │ │
│    │                                                      │ │
│    │ Tool 1: start_call                                  │ │
│    │   → Check cache: MISS                               │ │
│    │   → Query DB: No existing patient                   │ │
│    │   → Cache result                                    │ │
│    │   → Return: {result: "new_call"}                    │ │
│    │   Time: 30ms                                        │ │
│    │                                                      │ │
│    │ Tool 2: validate_field                              │ │
│    │   → Validate: "5551234567" is 10 digits            │ │
│    │   → Return: {valid: true}                           │ │
│    │   Time: 5ms                                         │ │
│    │                                                      │ │
│    │ Tool 3: update_field                                │ │
│    │   → Get session: CACHE HIT                          │ │
│    │   → Update: draft.collected["phone_number"]        │ │
│    │   → Invalidate progress cache                       │ │
│    │   → Return: {result: "success"}                     │ │
│    │   Time: 2ms                                         │ │
│    │                                                      │ │
│    │ Tool 4: check_duplicate                             │ │
│    │   → Check cache: HIT (from start_call)             │ │
│    │   → Return: {duplicate: false}                      │ │
│    │   Time: 1ms                                         │ │
│    │                                                      │ │
│    │ Total time: max(30, 5, 2, 1) = 30ms                │ │
│    └─────────────────────────────────────────────────────┘ │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (Results returned to LLM)
┌─────────────────────────────────────────────────────────────┐
│ 8. Gemini receives tool results:                            │
│    - start_call: {result: "new_call"}                       │
│    - validate_field: {valid: true}                          │
│    - update_field: {result: "success"}                      │
│    - check_duplicate: {duplicate: false}                    │
│                                                              │
│    Generates response: "Great! What's your first name?"     │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼ (TTS)
┌─────────────────────────────────────────────────────────────┐
│ 9. User hears: "Great! What's your first name?"            │
└─────────────────────────────────────────────────────────────┘
```


## 3. Tool Execution Flow (Detailed)

```
┌─────────────────────────────────────────────────────────────────┐
│              GEMINI LLM GENERATES TOOL CALLS                     │
│                                                                   │
│  Conversation History:                                           │
│  [                                                                │
│    {role: "user", content: "555-123-4567"},                     │
│    {role: "assistant", tool_calls: [                            │
│      {name: "validate_field", arguments: {...}},                │
│      {name: "update_field", arguments: {...}},                  │
│    ]}                                                             │
│  ]                                                                │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│           PIPECAT BOT: _execute_tools()                          │
│                                                                   │
│  1. Extract tool calls from LLM response                         │
│  2. Prepare batch_calls list                                     │
│  3. Call: execute_tools_batch(call_id, db, batch_calls)         │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│         TOOL WORKFLOW: execute_tools_batch()                     │
│                                                                   │
│  workflow = ToolWorkflow(call_id, db)                           │
│                                                                   │
│  tasks = [                                                        │
│    workflow.execute_tool("validate_field", {...}, timeout=5.0), │
│    workflow.execute_tool("update_field", {...}, timeout=5.0),   │
│  ]                                                                │
│                                                                   │
│  results = await asyncio.gather(*tasks)  # PARALLEL             │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│         TOOL WORKFLOW: execute_tool() [PER TOOL]                 │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ START: Tool execution with timeout                         │ │
│  │ start_time = now()                                         │ │
│  │ metrics["tools_executed"] += 1                             │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│                       ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ TRY: Execute with 5-second timeout                         │ │
│  │   result = await asyncio.wait_for(                         │ │
│  │     self._route_tool(tool_name, arguments),                │ │
│  │     timeout=5.0                                            │ │
│  │   )                                                         │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│                       ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ _route_tool(): Find handler                                │ │
│  │   handlers = {                                             │ │
│  │     "validate_field": _handle_validate_field,             │ │
│  │     "update_field": _handle_update_field,                 │ │
│  │     ...                                                     │ │
│  │   }                                                         │ │
│  │   handler = handlers[tool_name]                            │ │
│  │   return await handler(arguments)                          │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│                       ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ HANDLER: Execute tool logic                                │ │
│  │   (See detailed handler flow below)                        │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│                       ▼                                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ SUCCESS: Log execution                                     │ │
│  │   execution_time = now() - start_time                      │ │
│  │   _log_execution(tool_name, result, execution_time)        │ │
│  │   return result                                            │ │
│  └────────────────────┬───────────────────────────────────────┘ │
│                       │                                          │
│  ┌────────────────────┴───────────────────────────────────────┐ │
│  │ EXCEPT TimeoutError:                                       │ │
│  │   fallback = _get_fallback_response(tool_name, "timeout") │ │
│  │   _log_execution(tool_name, fallback, timeout, error)      │ │
│  │   return fallback                                          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ EXCEPT Exception:                                          │ │
│  │   fallback = _get_fallback_response(tool_name, error)     │ │
│  │   _log_execution(tool_name, fallback, time, error)         │ │
│  │   return fallback                                          │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## 4. Tool Handler Flow (Example: validate_field)

```
┌─────────────────────────────────────────────────────────────────┐
│         _handle_validate_field(arguments)                        │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. Extract arguments                                             │
│    field_name = arguments.get("field_name")                     │
│    field_value = arguments.get("field_value")                   │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Validate arguments                                            │
│    if not field_name or field_value is None:                    │
│      return {valid: false, error: "missing params"}             │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Create test data with dummy required fields                  │
│    dummy_patient = {                                             │
│      "first_name": "Test",                                       │
│      "last_name": "User",                                        │
│      ...                                                          │
│      field_name: field_value  # The field being validated       │
│    }                                                              │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. TRY: Validate with Pydantic                                  │
│    PatientCreate(**dummy_patient)                               │
│    ✓ Validation passes                                          │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Return success                                                │
│    return {                                                       │
│      valid: true,                                                │
│      field_name: "phone_number",                                │
│      message: "phone_number is valid."                          │
│    }                                                              │
└─────────────────────────────────────────────────────────────────┘

                             OR

┌─────────────────────────────────────────────────────────────────┐
│ 4. EXCEPT: Validation fails                                     │
│    ✗ ValueError: "Phone number must be 10 digits"              │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Extract error message                                         │
│    error_msg = "Phone number must be 10 digits"                 │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. Return error                                                  │
│    return {                                                       │
│      valid: false,                                               │
│      field_name: "phone_number",                                │
│      error: "Phone number must be 10 digits",                   │
│      message: "Invalid phone_number: Phone number must be..."   │
│    }                                                              │
└─────────────────────────────────────────────────────────────────┘
```

## 5. Caching Flow (Example: check_duplicate)

```
┌─────────────────────────────────────────────────────────────────┐
│         _handle_check_duplicate(arguments)                       │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. Extract phone number                                          │
│    phone_number = arguments.get("phone_number")                 │
│    → "5551234567"                                                │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Check cache first                                             │
│    if phone_number in self._duplicate_cache:                    │
└────────────────────────────┬──────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
        ┌───────────────────┐  ┌──────────────────┐
        │   CACHE HIT       │  │   CACHE MISS     │
        └─────────┬─────────┘  └────────┬─────────┘
                  │                     │
                  ▼                     ▼
    ┌──────────────────────┐  ┌─────────────────────────────┐
    │ 3a. Get from cache   │  │ 3b. Query database          │
    │ existing = cache[ph] │  │ existing = await            │
    │ metrics["cache_hits"]│  │   patient_service           │
    │   += 1               │  │   .check_duplicate_by_phone │
    │ Time: <1ms           │  │ metrics["cache_misses"] += 1│
    └─────────┬────────────┘  │ metrics["db_queries"] += 1  │
              │               │ Time: 20-50ms               │
              │               └────────┬────────────────────┘
              │                        │
              │                        ▼
              │               ┌─────────────────────────────┐
              │               │ 4. Store in cache           │
              │               │ cache[phone_number] =       │
              │               │   existing                  │
              │               └────────┬────────────────────┘
              │                        │
              └────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Check if duplicate found                                     │
│    if existing:                                                  │
└────────────────────────────┬──────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
        ┌───────────────────┐  ┌──────────────────┐
        │   DUPLICATE       │  │   NO DUPLICATE   │
        └─────────┬─────────┘  └────────┬─────────┘
                  │                     │
                  ▼                     ▼
    ┌──────────────────────┐  ┌─────────────────────────────┐
    │ 6a. Mark as update   │  │ 6b. Return no duplicate     │
    │ draft = _get_session │  │ return {                    │
    │ draft.is_update=true │  │   duplicate: false,         │
    │ draft.patient_id=id  │  │   message: "No existing..." │
    │ return {             │  │ }                           │
    │   duplicate: true,   │  └─────────────────────────────┘
    │   patient_id: id,    │
    │   existing_name: ... │
    │ }                    │
    └──────────────────────┘
```


## 6. Session Management Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    SESSION LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────┘

CALL START
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ 1. First tool call in conversation                              │
│    workflow = ToolWorkflow(call_id="call-123", db)              │
│    workflow._session_cache = None  # Empty cache                │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Tool calls _get_session()                                    │
│    if self._session_cache is None:  # CACHE MISS               │
│      metrics["cache_misses"] += 1                               │
│      self._session_cache = session_service                      │
│        .get_or_create_session(call_id)                          │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Session Service creates new session                          │
│    draft = PatientDraft(                                         │
│      call_id="call-123",                                         │
│      collected={},                                               │
│      confirmed=False,                                            │
│      patient_id=None,                                            │
│      is_update=False,                                            │
│      idempotency_key=None                                        │
│    )                                                              │
│    _session_store["call-123"] = draft                           │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Return session to tool                                        │
│    return draft                                                  │
└─────────────────────────────────────────────────────────────────┘


SUBSEQUENT TOOL CALLS (CACHE HITS)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ 5. Next tool calls _get_session()                               │
│    if self._session_cache is None:  # FALSE                    │
│    else:                                                         │
│      metrics["cache_hits"] += 1                                 │
│      return self._session_cache  # CACHE HIT                    │
│    Time: <1ms (vs 1-2ms for lookup)                            │
└─────────────────────────────────────────────────────────────────┘


FIELD UPDATE (CACHE INVALIDATION)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ 6. update_field() modifies session                              │
│    draft = self._get_session()  # CACHE HIT                    │
│    draft.collected["first_name"] = "John"                       │
│    self._invalidate_progress_cache()  # Clear dependent cache  │
└─────────────────────────────────────────────────────────────────┘


SAVE PATIENT (CACHE INVALIDATION)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ 7. save_patient() commits to DB                                 │
│    session_service.mark_confirmed(call_id, patient_id, key)    │
│    self._invalidate_session_cache()  # Clear all caches        │
└─────────────────────────────────────────────────────────────────┘


CALL END (CLEANUP)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ 8. end_call() cleans up                                         │
│    session_service.delete_session(call_id)                      │
│    self._invalidate_session_cache()                             │
│    _session_store.pop("call-123")  # Remove from memory        │
└─────────────────────────────────────────────────────────────────┘
```

## 7. Confirmation Flow (Critical Path)

```
┌─────────────────────────────────────────────────────────────────┐
│              CONFIRMATION ENFORCEMENT FLOW                       │
└─────────────────────────────────────────────────────────────────┘

STEP 1: CHECK READINESS
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ LLM: "Let me confirm your information..."                       │
│ Calls: confirm_ready()                                           │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _handle_confirm_ready()                                          │
│   draft = _get_session()                                         │
│   required = ["first_name", "last_name", ...]                   │
│   missing = [f for f in required if f not in draft.collected]  │
└────────────────────────────┬──────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
        ┌───────────────────┐  ┌──────────────────┐
        │   ALL COLLECTED   │  │   MISSING FIELDS │
        └─────────┬─────────┘  └────────┬─────────┘
                  │                     │
                  ▼                     ▼
    ┌──────────────────────┐  ┌─────────────────────────────┐
    │ Return: ready=true   │  │ Return: ready=false         │
    │ LLM proceeds to      │  │ missing_fields=["city",     │
    │ read back            │  │   "state"]                  │
    │                      │  │ LLM: "I still need your     │
    │                      │  │   city and state"           │
    └──────────┬───────────┘  └─────────────────────────────┘
               │
               ▼

STEP 2: READ BACK TO USER
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ LLM: "Your name is John Smith, born 01/15/1985, phone          │
│      555-123-4567, address 123 Main St, Boston MA 02101.       │
│      Does everything look correct?"                             │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ User: "Yes, that's correct"                                     │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼

STEP 3: MARK CONFIRMED
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ LLM calls: confirm_completed()                                   │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _handle_confirm_completed()                                      │
│   draft = _get_session()                                         │
│   draft.confirmed = True                                         │
│   draft.collected["_confirmation_status"] = "confirmed"         │
│   draft.collected["_confirmation_timestamp"] = now()            │
│   return {confirmed: true}                                       │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼

STEP 4: SAVE PATIENT (WITH VALIDATION)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ LLM calls: save_patient({...all fields...})                     │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _handle_save_patient(arguments)                                  │
│   draft = _get_session()                                         │
│                                                                   │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ VALIDATION 1: Check confirmation                        │  │
│   │ if not draft.confirmed:                                 │  │
│   │   return {error: "not_confirmed"}                       │  │
│   │   ❌ BLOCKED - Must confirm first                       │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                   │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ VALIDATION 2: Check required fields                     │  │
│   │ required = ["first_name", "last_name", ...]            │  │
│   │ missing = [f for f in required if f not in arguments]  │  │
│   │ if missing:                                             │  │
│   │   return {error: "missing_required_fields"}            │  │
│   │   ❌ BLOCKED - Missing fields                          │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                   │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ VALIDATION 3: Idempotency check                         │  │
│   │ if draft.idempotency_key == tool_call_id:              │  │
│   │   return {result: "already_saved"}                      │  │
│   │   ✓ Already saved - Skip duplicate write               │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                   │
│   ✓ All validations passed                                      │
│   patient = await patient_service.create_patient(db, data)     │
│   await db.commit()                                              │
│   return {result: "success", patient_id: id}                    │
└─────────────────────────────────────────────────────────────────┘
```

## 8. Error Handling & Fallback Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    ERROR SCENARIOS                               │
└─────────────────────────────────────────────────────────────────┘

SCENARIO 1: TOOL TIMEOUT
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ Tool execution takes > 5 seconds                                 │
│ (e.g., database connection slow)                                │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ asyncio.TimeoutError raised                                      │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ _get_fallback_response(tool_name, "timeout")                    │
│   For save_patient:                                              │
│     return {                                                      │
│       result: "error",                                           │
│       error: "timeout",                                          │
│       message: "I'm having trouble saving... try calling back", │
│       fallback: true                                             │
│     }                                                             │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ LLM receives fallback response                                   │
│ Generates: "I'm having trouble saving the information right     │
│            now. Could you please try calling back in a few      │
│            minutes?"                                             │
│ ✓ Conversation continues gracefully                             │
└─────────────────────────────────────────────────────────────────┘


SCENARIO 2: VALIDATION ERROR
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ User provides invalid data: "123" for phone number              │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ validate_field(field_name="phone_number", field_value="123")   │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Pydantic validation fails                                        │
│ ValueError: "Phone number must be exactly 10 digits"           │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Return error response (NOT fallback)                            │
│   return {                                                        │
│     valid: false,                                                │
│     error: "Phone number must be exactly 10 digits",           │
│     message: "Invalid phone_number: Phone number must be..."    │
│   }                                                               │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ LLM receives error response                                      │
│ Generates: "I need a 10-digit phone number. Could you provide  │
│            that again?"                                          │
│ ✓ User gets specific feedback                                   │
└─────────────────────────────────────────────────────────────────┘


SCENARIO 3: DATABASE ERROR
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ save_patient() attempts to write to database                    │
│ Database connection lost                                         │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Exception: "Connection refused"                                 │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Caught in execute_tool()                                         │
│ _get_fallback_response("save_patient", "Connection refused")   │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Return fallback (critical tool)                                 │
│   return {                                                        │
│     result: "error",                                             │
│     error: "Connection refused",                                │
│     message: "I'm having trouble saving... try calling back",   │
│     fallback: true                                               │
│   }                                                               │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ LLM receives fallback                                            │
│ ✓ Conversation continues with error message                     │
│ ✓ User informed to try again later                              │
└─────────────────────────────────────────────────────────────────┘
```


## 9. Performance Optimization Flow

```
┌─────────────────────────────────────────────────────────────────┐
│           PERFORMANCE: BEFORE vs AFTER OPTIMIZATION              │
└─────────────────────────────────────────────────────────────────┘

BEFORE OPTIMIZATION (Sequential, No Caching)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ Tool 1: validate_field                                           │
│   → Get session: 1ms                                             │
│   → Validate: 5ms                                                │
│   Total: 6ms                                                     │
└────────────────────────────┬──────────────────────────────────┘
                             │ Sequential
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tool 2: update_field                                             │
│   → Get session: 1ms                                             │
│   → Update: 1ms                                                  │
│   Total: 2ms                                                     │
└────────────────────────────┬──────────────────────────────────┘
                             │ Sequential
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tool 3: check_duplicate                                          │
│   → Get session: 1ms                                             │
│   → Query DB: 30ms                                               │
│   Total: 31ms                                                    │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
Total Time: 6ms + 2ms + 31ms = 39ms


AFTER OPTIMIZATION (Parallel, With Caching)
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ PARALLEL EXECUTION                                               │
│                                                                   │
│ ┌─────────────────────┐  ┌─────────────────────┐               │
│ │ Tool 1:             │  │ Tool 2:             │               │
│ │ validate_field      │  │ update_field        │               │
│ │ → Get session: <1ms │  │ → Get session: <1ms │               │
│ │   (CACHE HIT)       │  │   (CACHE HIT)       │               │
│ │ → Validate: 5ms     │  │ → Update: 1ms       │               │
│ │ Total: 5ms          │  │ Total: 1ms          │               │
│ └─────────────────────┘  └─────────────────────┘               │
│                                                                   │
│ ┌─────────────────────────────────────────────┐                │
│ │ Tool 3: check_duplicate                     │                │
│ │ → Get session: <1ms (CACHE HIT)            │                │
│ │ → Check cache: <1ms (CACHE HIT)            │                │
│ │ Total: <1ms                                 │                │
│ └─────────────────────────────────────────────┘                │
│                                                                   │
│ Total Time: max(5ms, 1ms, <1ms) = 5ms                          │
└─────────────────────────────────────────────────────────────────┘

IMPROVEMENT: 39ms → 5ms (87% faster!)


CACHE PERFORMANCE BREAKDOWN
══════════════════════════════════════════════════════════════════
┌─────────────────────────────────────────────────────────────────┐
│ Typical call with 13 tool executions:                           │
│                                                                   │
│ Session Cache:                                                   │
│   - First call: MISS (1ms)                                       │
│   - Next 12 calls: HIT (<0.1ms each)                           │
│   - Hit rate: 92%                                                │
│   - Time saved: 12ms                                             │
│                                                                   │
│ Duplicate Cache:                                                 │
│   - First check: MISS (30ms)                                     │
│   - Next 2 checks: HIT (<1ms each)                             │
│   - Hit rate: 67%                                                │
│   - Time saved: 60ms                                             │
│                                                                   │
│ Progress Cache:                                                  │
│   - First calc: MISS (5ms)                                       │
│   - Next 2 calcs: HIT (<1ms each)                              │
│   - Hit rate: 67%                                                │
│   - Time saved: 10ms                                             │
│                                                                   │
│ Total cache savings: ~82ms per call                             │
└─────────────────────────────────────────────────────────────────┘
```

## 10. Complete Registration Flow (End-to-End)

```
┌─────────────────────────────────────────────────────────────────┐
│              COMPLETE PATIENT REGISTRATION FLOW                  │
│                    (Typical 3-minute call)                       │
└─────────────────────────────────────────────────────────────────┘

TIME: 0:00 - CALL START
══════════════════════════════════════════════════════════════════
User: "Hi, I'd like to register"
  ↓ STT (100ms)
LLM: Generates greeting
  ↓ TTS (200ms)
Agent: "Hello! I'd be happy to help. What's your phone number?"

TIME: 0:10 - PHONE NUMBER
══════════════════════════════════════════════════════════════════
User: "555-123-4567"
  ↓ STT (100ms)
LLM: Calls tools [start_call, validate_field, update_field, check_duplicate]
  ↓ Tools execute in parallel (30ms)
  ↓ LLM generates response (500ms)
  ↓ TTS (200ms)
Agent: "Great! What's your first name?"

TIME: 0:20 - FIRST NAME
══════════════════════════════════════════════════════════════════
User: "John"
  ↓ STT (100ms)
LLM: Calls [update_field]
  ↓ Tool executes (1ms - cached session)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "And your last name?"

TIME: 0:30 - LAST NAME
══════════════════════════════════════════════════════════════════
User: "Smith"
  ↓ STT (100ms)
LLM: Calls [update_field]
  ↓ Tool executes (1ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "What's your date of birth?"

TIME: 0:40 - DATE OF BIRTH
══════════════════════════════════════════════════════════════════
User: "January 15th, 1985"
  ↓ STT (100ms)
LLM: Calls [validate_field, update_field]
  ↓ Tools execute in parallel (5ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "And your sex?"

TIME: 0:50 - SEX
══════════════════════════════════════════════════════════════════
User: "Male"
  ↓ STT (100ms)
LLM: Calls [update_field]
  ↓ Tool executes (1ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "What's your street address?"

TIME: 1:00 - ADDRESS
══════════════════════════════════════════════════════════════════
User: "123 Main Street"
  ↓ STT (100ms)
LLM: Calls [update_field]
  ↓ Tool executes (1ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "What city?"

TIME: 1:10 - CITY
══════════════════════════════════════════════════════════════════
User: "Boston"
  ↓ STT (100ms)
LLM: Calls [update_field]
  ↓ Tool executes (1ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "And the state?"

TIME: 1:20 - STATE
══════════════════════════════════════════════════════════════════
User: "Massachusetts"
  ↓ STT (100ms)
LLM: Calls [validate_field, update_field]
  ↓ Tools execute (5ms)
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "What's your zip code?"

TIME: 1:30 - ZIP CODE
══════════════════════════════════════════════════════════════════
User: "02101"
  ↓ STT (100ms)
LLM: Calls [validate_field, update_field, get_progress]
  ↓ Tools execute in parallel (5ms)
  ↓ get_progress returns: 100% complete
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "I can also collect your email, insurance, and emergency contact.
       Would you like to provide any of those?"

TIME: 1:45 - OPTIONAL FIELDS
══════════════════════════════════════════════════════════════════
User: "No, that's all"
  ↓ STT (100ms)
LLM: Calls [confirm_ready]
  ↓ Tool validates all required fields (1ms - cached)
  ↓ Returns: ready=true
  ↓ LLM generates read-back (500ms)
  ↓ TTS (300ms - longer message)
Agent: "Let me confirm your information. Your name is John Smith,
       born January 15, 1985, male, phone 555-123-4567,
       address 123 Main Street, Boston, Massachusetts, 02101.
       Does everything look correct?"

TIME: 2:00 - CONFIRMATION
══════════════════════════════════════════════════════════════════
User: "Yes, that's correct"
  ↓ STT (100ms)
LLM: Calls [confirm_completed, save_patient]
  ↓ confirm_completed: Marks confirmed=true (1ms)
  ↓ save_patient: Validates & saves to DB (100ms)
  ↓ Returns: success, patient_id
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "Perfect! You're all set, John. We have availability for
       a first appointment next week. Would you like to schedule that?"

TIME: 2:15 - APPOINTMENT
══════════════════════════════════════════════════════════════════
User: "Yes, Monday morning would be great"
  ↓ STT (100ms)
LLM: Calls [schedule_appointment]
  ↓ Tool generates appointment (1ms)
  ↓ Returns: Monday 9:00 AM
  ↓ LLM generates (500ms)
  ↓ TTS (200ms)
Agent: "Excellent! I've scheduled you for Monday, March 10th at 9:00 AM.
       Have a great day!"

TIME: 2:30 - CALL END
══════════════════════════════════════════════════════════════════
LLM: Calls [end_call]
  ↓ Flushes transcript buffer
  ↓ Saves transcript to call_logs (75ms)
  ↓ Cleans up session
  ↓ Returns: success
Call ends

TOTAL CALL DURATION: 2:30
TOTAL TOOL EXECUTIONS: 18
TOTAL TOOL TIME: ~350ms (with caching)
CACHE HIT RATE: 85%
DATABASE QUERIES: 3 (check_duplicate, save_patient, log_call)
```

## 11. Performance Metrics Summary

```
┌─────────────────────────────────────────────────────────────────┐
│              PERFORMANCE METRICS PER CALL                        │
└─────────────────────────────────────────────────────────────────┘

TIMING BREAKDOWN
══════════════════════════════════════════════════════════════════
Component                    Time        Percentage
─────────────────────────────────────────────────────────────────
User speaking                60s         40%
Agent speaking (TTS)         45s         30%
STT processing               2s          1.3%
LLM generation               9s          6%
Tool execution               0.35s       0.2%
Network latency              3s          2%
Silence/pauses               30s         20%
─────────────────────────────────────────────────────────────────
TOTAL                        150s        100%

TOOL EXECUTION BREAKDOWN
══════════════════════════════════════════════════════════════════
Tool                    Calls   Avg Time    Total Time
─────────────────────────────────────────────────────────────────
start_call              1       30ms        30ms
validate_field          5       5ms         25ms
update_field            9       1ms         9ms
check_duplicate         1       <1ms        <1ms (cached)
get_progress            2       <1ms        <1ms (cached)
confirm_ready           1       1ms         1ms
confirm_completed       1       1ms         1ms
save_patient            1       100ms       100ms
schedule_appointment    1       1ms         1ms
end_call                1       75ms        75ms
─────────────────────────────────────────────────────────────────
TOTAL                   23                  ~243ms

CACHE PERFORMANCE
══════════════════════════════════════════════════════════════════
Metric                          Value
─────────────────────────────────────────────────────────────────
Total cache operations          40
Cache hits                      34
Cache misses                    6
Cache hit rate                  85%
Time saved by caching           ~82ms
─────────────────────────────────────────────────────────────────

DATABASE OPERATIONS
══════════════════════════════════════════════════────────────────
Operation                       Count   Time
─────────────────────────────────────────────────────────────────
check_duplicate_by_phone        1       30ms
create_patient                  1       50ms
log_call                        1       25ms
─────────────────────────────────────────────────────────────────
TOTAL                           3       105ms
```

This comprehensive diagram set shows the complete LLM tool use workflow from user input through tool execution, caching, error handling, and final response generation!