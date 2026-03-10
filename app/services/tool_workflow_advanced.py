"""
Advanced Tool Workflow Engine - N8N-style execution with detailed step tracking.

This is an ultra-optimized workflow engine with:
- Step-by-step execution tracking
- Conditional branching
- Retry logic with exponential backoff
- Circuit breaker pattern
- Advanced caching strategies
- Parallel execution optimization
- Detailed execution logs
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.patient import PatientCreate, PatientUpdate
from app.services import patient_service, session_service

logger = structlog.get_logger()


class StepStatus(Enum):
    """Step execution status."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class StepType(Enum):
    """Type of workflow step."""
    TOOL = "tool"
    CONDITION = "condition"
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    RETRY = "retry"


@dataclass
class StepResult:
    """Result of a workflow step execution."""
    step_id: str
    step_name: str
    step_type: StepType
    status: StepStatus
    result: dict[str, Any]
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    retry_count: int = 0
    cache_hit: bool = False
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStep:
    """Definition of a workflow step."""
    id: str
    name: str
    type: StepType
    handler: Optional[Callable] = None
    condition: Optional[Callable] = None
    retry_config: Optional[dict] = None
    timeout: float = 5.0
    cache_key: Optional[str] = None
    depends_on: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CircuitBreaker:
    """Circuit breaker to prevent cascading failures."""
    
    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half_open
    
    def record_success(self):
        """Record successful execution."""
        self.failures = 0
        self.state = "closed"
    
    def record_failure(self):
        """Record failed execution."""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.failure_threshold:
            self.state = "open"
            logger.warning("circuit_breaker_opened", failures=self.failures)
    
    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self.state == "closed":
            return True
        
        if self.state == "open":
            # Check if timeout has passed
            if self.last_failure_time and (time.time() - self.last_failure_time) > self.timeout:
                self.state = "half_open"
                logger.info("circuit_breaker_half_open")
                return True
            return False
        
        # half_open state - allow one attempt
        return True


class AdvancedToolWorkflow:
    """
    Advanced workflow engine with n8n-style execution.
    
    Features:
    - Step-by-step execution with detailed tracking
    - Conditional branching based on results
    - Retry logic with exponential backoff
    - Circuit breaker pattern
    - Advanced caching with TTL
    - Parallel and sequential execution
    - Detailed execution logs
    """
    
    def __init__(self, call_id: str, db: AsyncSession):
        self.call_id = call_id
        self.db = db
        
        # Execution tracking
        self.steps: list[WorkflowStep] = []
        self.step_results: dict[str, StepResult] = {}
        self.execution_order: list[str] = []
        
        # Performance caching
        self._session_cache: Optional[Any] = None
        self._cache: dict[str, tuple[Any, float]] = {}  # (value, timestamp)
        self._cache_ttl = 300.0  # 5 minutes
        
        # Circuit breakers per tool
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        
        # Performance metrics
        self._metrics = {
            "total_steps": 0,
            "successful_steps": 0,
            "failed_steps": 0,
            "skipped_steps": 0,
            "retried_steps": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "total_execution_time_ms": 0.0,
        }
        
        # Workflow state
        self._workflow_started_at: Optional[datetime] = None
        self._workflow_completed_at: Optional[datetime] = None
        self._workflow_status = "pending"
    
    # ==================== Cache Management ====================
    
    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get value from cache with TTL check."""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if (time.time() - timestamp) < self._cache_ttl:
                self._metrics["cache_hits"] += 1
                logger.debug("cache_hit", call_id=self.call_id, key=key)
                return value
            else:
                # Expired
                del self._cache[key]
        
        self._metrics["cache_misses"] += 1
        return None
    
    def _set_in_cache(self, key: str, value: Any):
        """Set value in cache with timestamp."""
        self._cache[key] = (value, time.time())
    
    def _invalidate_cache(self, pattern: Optional[str] = None):
        """Invalidate cache entries matching pattern."""
        if pattern is None:
            self._cache.clear()
        else:
            keys_to_delete = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self._cache[key]
    
    def _get_session(self):
        """Get session with caching."""
        if self._session_cache is None:
            self._session_cache = session_service.get_or_create_session(self.call_id)
        return self._session_cache
    
    # ==================== Circuit Breaker ====================
    
    def _get_circuit_breaker(self, tool_name: str) -> CircuitBreaker:
        """Get or create circuit breaker for tool."""
        if tool_name not in self._circuit_breakers:
            self._circuit_breakers[tool_name] = CircuitBreaker()
        return self._circuit_breakers[tool_name]
    
    # ==================== Step Execution ====================
    
    async def execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
    ) -> StepResult:
        """
        Execute a single workflow step with full tracking.
        
        Args:
            step: Step definition
            context: Execution context with previous results
        
        Returns:
            StepResult with execution details
        """
        step_result = StepResult(
            step_id=step.id,
            step_name=step.name,
            step_type=step.type,
            status=StepStatus.PENDING,
            result={},
            started_at=datetime.utcnow(),
        )
        
        self._metrics["total_steps"] += 1
        start_time = time.time()
        
        try:
            # Check circuit breaker
            circuit_breaker = self._get_circuit_breaker(step.name)
            if not circuit_breaker.can_execute():
                step_result.status = StepStatus.SKIPPED
                step_result.error = "circuit_breaker_open"
                self._metrics["skipped_steps"] += 1
                logger.warning("step_skipped_circuit_breaker", step_id=step.id)
                return step_result
            
            # Check cache
            if step.cache_key:
                cached_result = self._get_from_cache(step.cache_key)
                if cached_result is not None:
                    step_result.status = StepStatus.SUCCESS
                    step_result.result = cached_result
                    step_result.cache_hit = True
                    self._metrics["successful_steps"] += 1
                    circuit_breaker.record_success()
                    return step_result
            
            # Check condition
            if step.condition and not await step.condition(context):
                step_result.status = StepStatus.SKIPPED
                self._metrics["skipped_steps"] += 1
                logger.info("step_skipped_condition", step_id=step.id)
                return step_result
            
            # Execute with retry logic
            step_result.status = StepStatus.RUNNING
            result = await self._execute_with_retry(step, context)
            
            # Success
            step_result.status = StepStatus.SUCCESS
            step_result.result = result
            self._metrics["successful_steps"] += 1
            circuit_breaker.record_success()
            
            # Cache result
            if step.cache_key:
                self._set_in_cache(step.cache_key, result)
            
        except Exception as e:
            # Failure
            step_result.status = StepStatus.FAILED
            step_result.error = str(e)
            self._metrics["failed_steps"] += 1
            circuit_breaker.record_failure()
            logger.error("step_failed", step_id=step.id, error=str(e))
        
        finally:
            step_result.execution_time_ms = (time.time() - start_time) * 1000
            step_result.completed_at = datetime.utcnow()
            self._metrics["total_execution_time_ms"] += step_result.execution_time_ms
            
            # Store result
            self.step_results[step.id] = step_result
            self.execution_order.append(step.id)
        
        return step_result
    
    async def _execute_with_retry(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute step with retry logic."""
        retry_config = step.retry_config or {}
        max_retries = retry_config.get("max_retries", 0)
        backoff_multiplier = retry_config.get("backoff_multiplier", 2.0)
        initial_delay = retry_config.get("initial_delay", 1.0)
        
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    step.handler(context, self),
                    timeout=step.timeout,
                )
                return result
            
            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < max_retries:
                    delay = initial_delay * (backoff_multiplier ** attempt)
                    logger.warning(
                        "step_retry_timeout",
                        step_id=step.id,
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    self._metrics["retried_steps"] += 1
                    await asyncio.sleep(delay)
                else:
                    raise
            
            except Exception as e:
                last_error = e
                if attempt < max_retries and retry_config.get("retry_on_error", False):
                    delay = initial_delay * (backoff_multiplier ** attempt)
                    logger.warning(
                        "step_retry_error",
                        step_id=step.id,
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(e),
                    )
                    self._metrics["retried_steps"] += 1
                    await asyncio.sleep(delay)
                else:
                    raise
        
        raise last_error
    
    # ==================== Workflow Execution ====================
    
    async def execute_workflow(
        self,
        steps: list[WorkflowStep],
        initial_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute complete workflow with dependency resolution.
        
        Args:
            steps: List of workflow steps
            initial_context: Initial execution context
        
        Returns:
            Workflow execution summary
        """
        self._workflow_started_at = datetime.utcnow()
        self._workflow_status = "running"
        self.steps = steps
        
        context = initial_context or {}
        context["call_id"] = self.call_id
        context["workflow"] = self
        
        try:
            # Build dependency graph
            dependency_graph = self._build_dependency_graph(steps)
            
            # Execute steps in topological order
            executed_steps = set()
            
            while len(executed_steps) < len(steps):
                # Find steps ready to execute
                ready_steps = [
                    step for step in steps
                    if step.id not in executed_steps
                    and all(dep in executed_steps for dep in step.depends_on)
                ]
                
                if not ready_steps:
                    # Circular dependency or all remaining steps failed
                    break
                
                # Execute ready steps in parallel
                tasks = [
                    self.execute_step(step, context)
                    for step in ready_steps
                ]
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Update context with results
                for step, result in zip(ready_steps, results):
                    executed_steps.add(step.id)
                    if isinstance(result, StepResult) and result.status == StepStatus.SUCCESS:
                        context[step.id] = result.result
            
            self._workflow_status = "completed"
            
        except Exception as e:
            self._workflow_status = "failed"
            logger.error("workflow_failed", call_id=self.call_id, error=str(e))
        
        finally:
            self._workflow_completed_at = datetime.utcnow()
        
        return self.get_workflow_summary()
    
    def _build_dependency_graph(self, steps: list[WorkflowStep]) -> dict[str, list[str]]:
        """Build dependency graph for steps."""
        graph = {}
        for step in steps:
            graph[step.id] = step.depends_on
        return graph
    
    # ==================== Workflow Summary ====================
    
    def get_workflow_summary(self) -> dict[str, Any]:
        """Get detailed workflow execution summary."""
        total_time = (
            (self._workflow_completed_at - self._workflow_started_at).total_seconds() * 1000
            if self._workflow_started_at and self._workflow_completed_at
            else 0
        )
        
        return {
            "call_id": self.call_id,
            "status": self._workflow_status,
            "started_at": self._workflow_started_at.isoformat() if self._workflow_started_at else None,
            "completed_at": self._workflow_completed_at.isoformat() if self._workflow_completed_at else None,
            "total_execution_time_ms": round(total_time, 2),
            "metrics": {
                **self._metrics,
                "cache_hit_rate": round(
                    (self._metrics["cache_hits"] / 
                     (self._metrics["cache_hits"] + self._metrics["cache_misses"]) * 100)
                    if (self._metrics["cache_hits"] + self._metrics["cache_misses"]) > 0
                    else 0,
                    1
                ),
            },
            "steps": {
                step_id: {
                    "name": result.step_name,
                    "type": result.step_type.value,
                    "status": result.status.value,
                    "execution_time_ms": round(result.execution_time_ms, 2),
                    "retry_count": result.retry_count,
                    "cache_hit": result.cache_hit,
                    "error": result.error,
                }
                for step_id, result in self.step_results.items()
            },
            "execution_order": self.execution_order,
        }


# ==================== Tool Handlers for Advanced Workflow ====================

async def handle_validate_field_step(context: dict[str, Any], workflow: AdvancedToolWorkflow) -> dict[str, Any]:
    """Validate field handler for advanced workflow."""
    arguments = context.get("arguments", {})
    field_name = arguments.get("field_name", "")
    field_value = arguments.get("field_value", "")
    
    if not field_name or field_value is None:
        return {
            "valid": False,
            "field_name": field_name,
            "error": "field_name and field_value are required",
        }
    
    try:
        # Validate using Pydantic
        dummy_patient = {
            "first_name": "Test",
            "last_name": "User",
            "date_of_birth": "01/01/1990",
            "sex": "Male",
            "phone_number": "5555555555",
            "address_line_1": "123 Main St",
            "city": "Boston",
            "state": "MA",
            "zip_code": "02101",
        }
        dummy_patient[field_name] = field_value
        PatientCreate(**dummy_patient)
        
        return {
            "valid": True,
            "field_name": field_name,
            "message": f"{field_name} is valid.",
        }
    except Exception as e:
        error_msg = str(e)
        if "Value error," in error_msg:
            error_msg = error_msg.split("Value error,")[1].strip()
        return {
            "valid": False,
            "field_name": field_name,
            "error": error_msg,
        }


async def handle_check_duplicate_step(context: dict[str, Any], workflow: AdvancedToolWorkflow) -> dict[str, Any]:
    """Check duplicate handler for advanced workflow."""
    arguments = context.get("arguments", {})
    phone_number = arguments.get("phone_number", "")
    
    if not phone_number:
        return {"duplicate": False, "error": "phone_number is required"}
    
    # Check cache first
    cache_key = f"duplicate:{phone_number}"
    cached = workflow._get_from_cache(cache_key)
    if cached is not None:
        return cached
    
    # Query database
    existing = await patient_service.check_duplicate_by_phone(workflow.db, phone_number)
    
    result = {}
    if existing:
        draft = workflow._get_session()
        draft.is_update = True
        draft.patient_id = existing.id
        result = {
            "duplicate": True,
            "patient_id": str(existing.id),
            "existing_name": f"{existing.first_name} {existing.last_name}",
        }
    else:
        result = {"duplicate": False}
    
    # Cache result
    workflow._set_in_cache(cache_key, result)
    return result


async def handle_update_field_step(context: dict[str, Any], workflow: AdvancedToolWorkflow) -> dict[str, Any]:
    """Update field handler for advanced workflow."""
    arguments = context.get("arguments", {})
    field_name = arguments.get("field_name", "")
    field_value = arguments.get("field_value")
    
    if not field_name:
        return {"result": "error", "error": "field_name is required"}
    
    draft = workflow._get_session()
    draft.collected[field_name] = field_value
    
    # Invalidate dependent caches
    workflow._invalidate_cache("progress")
    
    return {
        "result": "success",
        "field_name": field_name,
        "field_value": field_value,
    }


async def handle_save_patient_step(context: dict[str, Any], workflow: AdvancedToolWorkflow) -> dict[str, Any]:
    """Save patient handler for advanced workflow."""
    arguments = context.get("arguments", {})
    draft = workflow._get_session()
    
    # Validation 1: Check confirmation
    if not draft.confirmed:
        return {"result": "error", "error": "not_confirmed"}
    
    # Validation 2: Check required fields
    required_fields = [
        "first_name", "last_name", "date_of_birth", "sex",
        "phone_number", "address_line_1", "city", "state", "zip_code"
    ]
    missing = [f for f in required_fields if f not in arguments or not arguments[f]]
    if missing:
        return {"result": "error", "error": "missing_required_fields", "missing_fields": missing}
    
    # Validation 3: Idempotency
    tool_call_id = f"save_patient_{workflow.call_id}"
    if draft.idempotency_key == tool_call_id:
        return {"result": "already_saved", "patient_id": str(draft.patient_id)}
    
    # Create patient
    try:
        patient_data = PatientCreate(**arguments)
        patient = await patient_service.create_patient(workflow.db, patient_data)
        await workflow.db.commit()
        
        session_service.mark_confirmed(workflow.call_id, patient.id, tool_call_id)
        
        return {
            "result": "success",
            "patient_id": str(patient.id),
            "message": f"Patient {patient.first_name} {patient.last_name} registered successfully.",
        }
    except Exception as e:
        await workflow.db.rollback()
        return {"result": "error", "error": str(e)}


# ==================== Workflow Builder ====================

def build_patient_registration_workflow(
    tool_calls: list[dict[str, Any]],
    call_id: str,
    db: AsyncSession,
) -> tuple[AdvancedToolWorkflow, list[WorkflowStep]]:
    """
    Build patient registration workflow from tool calls.
    
    Returns:
        Tuple of (workflow engine, list of steps)
    """
    workflow = AdvancedToolWorkflow(call_id, db)
    steps = []
    
    # Map tool names to handlers
    tool_handlers = {
        "validate_field": handle_validate_field_step,
        "check_duplicate": handle_check_duplicate_step,
        "update_field": handle_update_field_step,
        "save_patient": handle_save_patient_step,
        # Add more handlers as needed
    }
    
    # Build steps from tool calls
    for i, tool_call in enumerate(tool_calls):
        tool_name = tool_call.get("name", "")
        arguments = tool_call.get("arguments", {})
        
        handler = tool_handlers.get(tool_name)
        if not handler:
            continue
        
        # Determine dependencies
        depends_on = []
        if tool_name == "check_duplicate":
            # Depends on validate_field for phone
            depends_on = [f"step_{j}" for j, tc in enumerate(tool_calls[:i]) 
                         if tc.get("name") == "validate_field"]
        elif tool_name == "save_patient":
            # Depends on all previous steps
            depends_on = [f"step_{j}" for j in range(i)]
        
        # Create step
        step = WorkflowStep(
            id=f"step_{i}",
            name=tool_name,
            type=StepType.TOOL,
            handler=handler,
            retry_config={
                "max_retries": 2 if tool_name in ["save_patient", "check_duplicate"] else 0,
                "backoff_multiplier": 2.0,
                "initial_delay": 0.5,
                "retry_on_error": tool_name in ["save_patient"],
            },
            timeout=10.0 if tool_name == "save_patient" else 5.0,
            cache_key=f"{tool_name}:{arguments.get('phone_number', '')}" if tool_name == "check_duplicate" else None,
            depends_on=depends_on,
            metadata={"arguments": arguments},
        )
        
        steps.append(step)
    
    return workflow, steps


# ==================== Convenience Functions ====================

async def execute_tools_advanced(
    call_id: str,
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Execute tools using advanced workflow engine.
    
    Args:
        call_id: Call identifier
        db: Database session
        tool_calls: List of tool calls with name and arguments
    
    Returns:
        Workflow execution summary
    """
    workflow, steps = build_patient_registration_workflow(tool_calls, call_id, db)
    
    # Prepare initial context
    initial_context = {
        "tool_calls": tool_calls,
    }
    
    # Add arguments to context for each step
    for i, tool_call in enumerate(tool_calls):
        initial_context[f"step_{i}_arguments"] = tool_call.get("arguments", {})
    
    # Execute workflow
    summary = await workflow.execute_workflow(steps, initial_context)
    
    # Extract results for each tool
    results = []
    for i, tool_call in enumerate(tool_calls):
        step_id = f"step_{i}"
        if step_id in workflow.step_results:
            step_result = workflow.step_results[step_id]
            results.append(step_result.result)
        else:
            results.append({"error": "step_not_executed"})
    
    return {
        "results": results,
        "summary": summary,
    }
