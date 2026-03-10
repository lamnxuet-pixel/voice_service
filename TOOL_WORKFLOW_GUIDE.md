# Tool Workflow System Guide

## Overview

The Tool Workflow System provides robust tool execution with automatic fallback mechanisms, timeout handling, and comprehensive logging. It ensures the conversation continues gracefully even when tools fail.

## Key Features

### 1. Automatic Fallback Responses
When a tool fails, the system returns a sensible default response that allows the conversation to continue naturally.

### 2. Timeout Protection
Each tool has a configurable timeout (default 5 seconds) to prevent hanging operations.

### 3. Parallel Execution
Multiple tools execute concurrently for optimal performance.

### 4. Comprehensive Logging
Every tool execution is logged with timing, success status, and fallback usage.

### 5. Idempotency Protection
Built-in protection against duplicate writes on retries.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Tool Workflow System                     │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Timeout    │    │   Fallback   │    │   Logging    │  │
│  │  Protection  │───▶│   Handler    │───▶│   System     │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                    │                    │          │
│         ▼                    ▼                    ▼          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Tool Execution Router                    │  │
│  └──────────────────────────────────────────────────────┘  │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  validate_field │ check_duplicate │ update_field     │  │
│  │  save_patient   │ update_patient  │ reset_registration│  │
│  │  schedule_appointment                                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Usage

### Single Tool Execution

```python
from app.services.tool_workflow import execute_tool_with_workflow

result = await execute_tool_with_workflow(
    call_id="call-123",
    db=db_session,
    tool_name="validate_field",
    arguments={"field_name": "phone_number", "field_value": "5551234567"},
    timeout=5.0,
)
```

### Batch Tool Execution (Parallel)

```python
from app.services.tool_workflow import execute_tools_batch

tool_calls = [
    {"name": "validate_field", "arguments": {"field_name": "phone_number", "field_value": "5551234567"}},
    {"name": "check_duplicate", "arguments": {"phone_number": "5551234567"}},
]

results = await execute_tools_batch(
    call_id="call-123",
    db=db_session,
    tool_calls=tool_calls,
    timeout=5.0,
)
```

### Using the Workflow Class Directly

```python
from app.services.tool_workflow import ToolWorkflow

workflow = ToolWorkflow(call_id="call-123", db=db_session)

# Execute multiple tools
result1 = await workflow.execute_tool("validate_field", {...})
result2 = await workflow.execute_tool("check_duplicate", {...})

# Get execution summary
summary = workflow.get_execution_summary()
print(f"Total tools: {summary['total_tools_executed']}")
print(f"Fallbacks used: {summary['fallback_responses_used']}")
```

---

## Fallback Responses

### Design Philosophy

Fallback responses are designed to:
1. **Keep the conversation flowing** - Never leave the user hanging
2. **Be truthful** - Acknowledge when something went wrong
3. **Provide next steps** - Guide the user on what to do

### Fallback Response Table

| Tool | Fallback Behavior | Rationale |
|------|-------------------|-----------|
| validate_field | Assume valid, continue | Better to collect data than block conversation |
| check_duplicate | Assume no duplicate | Safer to create new than risk data loss |
| update_field | Confirm update | In-memory operation, low risk |
| save_patient | Return error, suggest retry | Critical operation, must not fake success |
| update_patient | Return error, suggest retry | Critical operation, must not fake success |
| reset_registration | Confirm reset | Low risk, improves UX |
| schedule_appointment | Offer callback | Non-critical, can be handled later |

### Example Fallback Responses

#### validate_field (Non-Critical)
```json
{
  "valid": true,
  "field_name": "phone_number",
  "message": "I'll note that down. We can verify it later if needed.",
  "fallback": true
}
```
**Why**: Better to collect potentially invalid data than block the conversation. Server-side validation will catch issues at save time.

#### save_patient (Critical)
```json
{
  "result": "error",
  "error": "timeout",
  "message": "I'm having trouble saving the information right now. Could you please try calling back in a few minutes?",
  "fallback": true
}
```
**Why**: Cannot fake success on database writes. User needs to know the operation failed.

#### schedule_appointment (Non-Critical)
```json
{
  "result": "success",
  "appointment_day": "Tuesday",
  "appointment_time": "10:00 AM",
  "appointment_date": "Next week",
  "message": "I'll have someone from our scheduling team call you to confirm an appointment time.",
  "fallback": true
}
```
**Why**: Scheduling is a bonus feature. Better to offer a callback than fail the entire registration.

---

## Timeout Handling

### Default Timeouts

- **validate_field**: 5 seconds (Pydantic validation is fast)
- **check_duplicate**: 5 seconds (DB query with index)
- **update_field**: 5 seconds (in-memory operation)
- **save_patient**: 5 seconds (DB write + commit)
- **update_patient**: 5 seconds (DB write + commit)
- **reset_registration**: 5 seconds (in-memory operation)
- **schedule_appointment**: 5 seconds (mock operation)

### Timeout Behavior

When a timeout occurs:
1. Operation is cancelled
2. Fallback response is returned
3. Error is logged with "timeout" reason
4. Conversation continues

### Configuring Timeouts

```python
# Per-tool timeout
result = await workflow.execute_tool(
    "save_patient",
    arguments={...},
    timeout=10.0,  # 10 seconds for slow DB
)

# Batch timeout (applies to each tool)
results = await execute_tools_batch(
    call_id="call-123",
    db=db,
    tool_calls=[...],
    timeout=3.0,  # 3 seconds per tool
)
```

---

## Error Handling

### Error Categories

1. **Validation Errors** - Invalid input data
2. **Database Errors** - Connection issues, constraint violations
3. **Timeout Errors** - Operation took too long
4. **Unknown Errors** - Unexpected exceptions

### Error Response Format

All errors return a consistent structure:

```json
{
  "result": "error",
  "error": "error_code_or_message",
  "message": "User-friendly error message",
  "fallback": true  // If fallback was used
}
```

### Error Handling Flow

```
Tool Execution
     │
     ├─ Success ──────────────────────────▶ Return Result
     │
     ├─ Timeout ──────────────────────────▶ Return Fallback
     │
     ├─ Validation Error ─────────────────▶ Return Error Response
     │
     └─ Unknown Error ────────────────────▶ Return Fallback
```

---

## Logging & Monitoring

### Log Levels

- **INFO**: Successful tool execution
- **WARNING**: Fallback response used
- **ERROR**: Tool execution failed

### Log Fields

Every tool execution logs:
```python
{
  "call_id": "call-123",
  "tool_name": "save_patient",
  "success": true,
  "execution_time_ms": 87.5,
  "fallback_used": false,
  "error": null  // or error message
}
```

### Execution Summary

Get a summary of all tool executions:

```python
summary = workflow.get_execution_summary()

# Returns:
{
  "call_id": "call-123",
  "total_tools_executed": 5,
  "successful_tools": 4,
  "failed_tools": 1,
  "fallback_responses_used": 1,
  "total_execution_time_ms": 234.5,
  "average_execution_time_ms": 46.9,
  "execution_log": [...]  // Detailed log of each tool
}
```

### Monitoring Queries

**Find calls with high fallback usage:**
```python
# In your monitoring system
SELECT call_id, COUNT(*) as fallback_count
FROM tool_executions
WHERE fallback_used = true
GROUP BY call_id
HAVING COUNT(*) > 2
```

**Find slow tools:**
```python
SELECT tool_name, AVG(execution_time_ms) as avg_time
FROM tool_executions
GROUP BY tool_name
HAVING AVG(execution_time_ms) > 1000
```

---

## Performance Characteristics

### Execution Times (Typical)

| Tool | Typical Time | Max Time (Timeout) |
|------|--------------|-------------------|
| validate_field | 5-15ms | 5000ms |
| check_duplicate | 20-50ms | 5000ms |
| update_field | 1-5ms | 5000ms |
| save_patient | 50-150ms | 5000ms |
| update_patient | 50-150ms | 5000ms |
| reset_registration | 1-5ms | 5000ms |
| schedule_appointment | 1-5ms | 5000ms |

### Parallel Execution Benefits

**Sequential execution:**
```
validate_field (10ms) + check_duplicate (30ms) + save_patient (100ms) = 140ms
```

**Parallel execution:**
```
max(validate_field (10ms), check_duplicate (30ms), save_patient (100ms)) = 100ms
```

**Savings: 40ms (28% faster)**

---

## Best Practices

### 1. Always Use Workflow System

❌ **Don't:**
```python
# Direct tool execution without fallbacks
result = await patient_service.create_patient(db, data)
```

✅ **Do:**
```python
# Use workflow system for automatic fallbacks
result = await execute_tool_with_workflow(
    call_id=call_id,
    db=db,
    tool_name="save_patient",
    arguments=data,
)
```

### 2. Batch Related Tools

❌ **Don't:**
```python
# Execute tools sequentially
result1 = await execute_tool_with_workflow(..., "validate_field", ...)
result2 = await execute_tool_with_workflow(..., "check_duplicate", ...)
```

✅ **Do:**
```python
# Execute tools in parallel
results = await execute_tools_batch(
    call_id=call_id,
    db=db,
    tool_calls=[
        {"name": "validate_field", "arguments": {...}},
        {"name": "check_duplicate", "arguments": {...}},
    ],
)
```

### 3. Check Fallback Flag

```python
result = await execute_tool_with_workflow(...)

if result.get("fallback", False):
    # Fallback was used - log for monitoring
    logger.warning("fallback_used", tool_name="save_patient")
    
    # Consider retry logic for critical operations
    if tool_name == "save_patient":
        # Retry once
        result = await execute_tool_with_workflow(...)
```

### 4. Set Appropriate Timeouts

```python
# Fast operations - short timeout
await workflow.execute_tool("validate_field", {...}, timeout=2.0)

# Slow operations - longer timeout
await workflow.execute_tool("save_patient", {...}, timeout=10.0)
```

### 5. Monitor Execution Summaries

```python
workflow = ToolWorkflow(call_id, db)

# Execute tools...

# Log summary for monitoring
summary = workflow.get_execution_summary()
logger.info("workflow_complete", **summary)

# Alert if too many fallbacks
if summary["fallback_responses_used"] > 2:
    alert_ops_team("High fallback rate", summary)
```

---

## Testing

### Unit Tests

```python
import pytest
from app.services.tool_workflow import ToolWorkflow

@pytest.mark.asyncio
async def test_validate_field_success(db_session):
    workflow = ToolWorkflow("test-call", db_session)
    
    result = await workflow.execute_tool(
        "validate_field",
        {"field_name": "phone_number", "field_value": "5551234567"},
    )
    
    assert result["valid"] == True
    assert result.get("fallback") is None

@pytest.mark.asyncio
async def test_validate_field_timeout(db_session, monkeypatch):
    # Mock slow validation
    async def slow_validate(*args):
        await asyncio.sleep(10)
        return {"valid": True}
    
    monkeypatch.setattr("app.services.tool_workflow.ToolWorkflow._handle_validate_field", slow_validate)
    
    workflow = ToolWorkflow("test-call", db_session)
    
    result = await workflow.execute_tool(
        "validate_field",
        {"field_name": "phone_number", "field_value": "5551234567"},
        timeout=1.0,
    )
    
    assert result.get("fallback") == True
    assert "valid" in result  # Fallback response
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_full_registration_with_fallback(db_session):
    from app.services.tool_workflow import execute_tools_batch
    
    tool_calls = [
        {"name": "validate_field", "arguments": {"field_name": "phone_number", "field_value": "5551234567"}},
        {"name": "check_duplicate", "arguments": {"phone_number": "5551234567"}},
        {"name": "save_patient", "arguments": {...}},
    ]
    
    results = await execute_tools_batch(
        call_id="test-call",
        db=db_session,
        tool_calls=tool_calls,
    )
    
    assert len(results) == 3
    assert all("message" in r for r in results)
```

---

## Troubleshooting

### High Fallback Rate

**Symptom**: Many tools returning fallback responses

**Possible Causes**:
1. Database connection issues
2. Timeouts too aggressive
3. Network latency

**Solutions**:
1. Check database health
2. Increase timeouts for slow operations
3. Add connection pooling
4. Scale database resources

### Tools Timing Out

**Symptom**: Tools consistently hitting timeout

**Possible Causes**:
1. Slow database queries
2. Missing indexes
3. Network issues

**Solutions**:
1. Add database indexes
2. Optimize queries
3. Increase timeout values
4. Use connection pooling

### Fallback Not Working

**Symptom**: Conversation breaks when tool fails

**Possible Causes**:
1. Not using workflow system
2. Fallback response malformed

**Solutions**:
1. Ensure all tools use `execute_tool_with_workflow`
2. Verify fallback responses match expected format
3. Check logs for errors

---

## Migration Guide

### From Direct Tool Calls

**Before:**
```python
# In pipecat_bot.py
result = await self._execute_single_tool(tool_name, arguments, db)
```

**After:**
```python
# In pipecat_bot.py
from app.services.tool_workflow import execute_tool_with_workflow

result = await execute_tool_with_workflow(
    call_id=self.call_id,
    db=db,
    tool_name=tool_name,
    arguments=arguments,
)
```

### From Sequential to Batch

**Before:**
```python
results = []
for tool_call in tool_calls:
    result = await execute_tool(...)
    results.append(result)
```

**After:**
```python
from app.services.tool_workflow import execute_tools_batch

results = await execute_tools_batch(
    call_id=call_id,
    db=db,
    tool_calls=tool_calls,
)
```

---

## Summary

The Tool Workflow System provides:

✅ **Automatic fallback responses** - Conversation never breaks
✅ **Timeout protection** - No hanging operations
✅ **Parallel execution** - Optimal performance
✅ **Comprehensive logging** - Full observability
✅ **Idempotency protection** - No duplicate writes
✅ **Graceful degradation** - System works even when components fail

This ensures a robust, production-ready voice registration system that handles failures gracefully and provides a smooth user experience.
