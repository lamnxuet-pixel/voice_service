"""
Unified tool executor that toggles between standard and advanced workflows.

This module provides a single interface for tool execution that automatically
selects the appropriate workflow engine based on configuration.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = structlog.get_logger()


async def execute_tools(
    call_id: str,
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Execute tools using configured workflow engine.
    
    Automatically selects between:
    - Standard workflow (fast, cached, production-ready)
    - Advanced workflow (detailed tracking, retry logic, circuit breakers)
    
    Args:
        call_id: Call identifier
        db: Database session
        tool_calls: List of tool calls with 'name' and 'arguments'
        timeout: Timeout per tool (used in standard mode)
    
    Returns:
        List of tool execution results
    """
    mode = settings.workflow_mode.lower()
    
    logger.info(
        "tool_execution_start",
        call_id=call_id,
        mode=mode,
        tool_count=len(tool_calls),
    )
    
    if mode == "advanced":
        return await _execute_advanced(call_id, db, tool_calls)
    else:
        return await _execute_standard(call_id, db, tool_calls, timeout)


async def _execute_standard(
    call_id: str,
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
    timeout: float,
) -> list[dict[str, Any]]:
    """Execute using standard workflow (fast, cached)."""
    from app.services.tool_workflow import execute_tools_batch
    
    logger.debug("using_standard_workflow", call_id=call_id)
    
    results = await execute_tools_batch(
        call_id=call_id,
        db=db,
        tool_calls=tool_calls,
        timeout=timeout,
    )
    
    return results


async def _execute_advanced(
    call_id: str,
    db: AsyncSession,
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Execute using advanced workflow (detailed tracking, retry logic)."""
    from app.services.tool_workflow_advanced import execute_tools_advanced
    
    logger.debug("using_advanced_workflow", call_id=call_id)
    
    result = await execute_tools_advanced(
        call_id=call_id,
        db=db,
        tool_calls=tool_calls,
    )
    
    # Log detailed summary
    summary = result.get("summary", {})
    logger.info(
        "advanced_workflow_complete",
        call_id=call_id,
        **summary.get("metrics", {}),
    )
    
    return result.get("results", [])


async def get_workflow_summary(call_id: str) -> dict[str, Any]:
    """
    Get workflow execution summary (advanced mode only).
    
    Args:
        call_id: Call identifier
    
    Returns:
        Workflow summary or empty dict if not available
    """
    mode = settings.workflow_mode.lower()
    
    if mode != "advanced":
        return {
            "mode": "standard",
            "message": "Detailed workflow summary only available in advanced mode",
        }
    
    # In advanced mode, summary would be stored somewhere
    # For now, return placeholder
    return {
        "mode": "advanced",
        "message": "Workflow summary available",
    }
