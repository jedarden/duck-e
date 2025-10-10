"""
Cost tracking wrapper utilities for OpenAI API calls
Provides synchronous and asynchronous wrappers for tracking token usage and costs
"""
import asyncio
from logging import getLogger
from typing import Dict, Any, Callable, Optional
from functools import wraps

logger = getLogger("uvicorn.error")


def track_openai_call(
    tracker,
    session_id: str,
    model: str,
    websocket,
    budget_threshold_warning: float = 4.0
):
    """
    Decorator to track OpenAI API call costs

    Args:
        tracker: SessionCostTracker instance
        session_id: Session ID for cost tracking
        model: Model name (gpt-5, gpt-5-mini, gpt-realtime)
        websocket: WebSocket connection for sending warnings
        budget_threshold_warning: Budget threshold for warnings (default $4.00)

    Returns:
        Decorated function with cost tracking
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # Execute the original function
                result = func(*args, **kwargs)

                # Check if result has usage information
                if hasattr(result, 'usage') and result.usage:
                    # Run cost tracking in event loop
                    loop = asyncio.get_event_loop()

                    # Track usage asynchronously
                    usage_task = tracker.track_usage(
                        session_id=session_id,
                        model=model,
                        input_tokens=result.usage.prompt_tokens,
                        output_tokens=result.usage.completion_tokens
                    )

                    # Get usage result
                    usage_result = loop.run_until_complete(usage_task)

                    logger.info(
                        f"{func.__name__} cost: ${usage_result['call_cost']:.6f}, "
                        f"Session total: ${usage_result['session_cost']:.6f}, "
                        f"Remaining: ${usage_result['remaining_budget_usd']:.2f}"
                    )

                    # Send budget warning at threshold
                    if (usage_result["session_cost"] >= budget_threshold_warning and
                        usage_result["budget_ok"]):
                        try:
                            warning_task = websocket.send_json({
                                "type": "budget_warning",
                                "message": f"You have used {(usage_result['session_cost']/5.0)*100:.0f}% of your session budget",
                                "session_cost": usage_result["session_cost"],
                                "remaining_budget": usage_result["remaining_budget_usd"]
                            })
                            loop.run_until_complete(warning_task)
                        except Exception as e:
                            logger.warning(f"Failed to send budget warning: {e}")

                    # Close session if budget exceeded
                    if not usage_result["budget_ok"]:
                        logger.warning(f"Budget exceeded for session {session_id} in {func.__name__}")
                        try:
                            close_tasks = [
                                websocket.send_json({
                                    "type": "budget_exceeded",
                                    "message": "Session budget limit exceeded",
                                    "session_cost": usage_result["session_cost"],
                                    "warnings": usage_result["warnings"]
                                }),
                                websocket.close(code=1008, reason="Budget limit exceeded")
                            ]
                            for task in close_tasks:
                                loop.run_until_complete(task)
                        except Exception as e:
                            logger.error(f"Failed to close WebSocket on budget exceeded: {e}")

                return result

            except Exception as e:
                logger.error(f"Error in cost tracking wrapper for {func.__name__}: {e}", exc_info=True)
                # Return original result even if cost tracking fails
                return func(*args, **kwargs)

        return wrapper
    return decorator


async def check_budget_before_call(
    tracker,
    session_id: str,
    error_message: str = "Unable to process request due to budget limits"
) -> bool:
    """
    Check if session has budget available before making API call

    Args:
        tracker: SessionCostTracker instance
        session_id: Session ID to check
        error_message: Error message to log if budget exceeded

    Returns:
        True if budget OK, False if budget exceeded
    """
    budget_status = await tracker.check_budget(
        session_id,
        tracker.session_costs.get(session_id, 0.0)
    )

    if not budget_status["budget_ok"]:
        logger.warning(
            f"Budget check failed for session {session_id}: {budget_status['warnings']}"
        )
        return False

    return True
