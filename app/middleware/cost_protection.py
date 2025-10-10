"""
Cost protection middleware for DUCK-E
Implements budget caps, session tracking, and circuit breaker for API costs
"""
from fastapi import Request, HTTPException, WebSocket
from fastapi.responses import JSONResponse
from typing import Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
import logging
from pydantic import BaseModel, Field
import redis.asyncio as redis
import json
import os
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# Prometheus metrics
api_cost_total = Counter(
    'api_cost_total_usd',
    'Total API costs in USD',
    ['model', 'session_id']
)

active_sessions = Gauge(
    'active_sessions_total',
    'Number of active sessions'
)

session_duration = Histogram(
    'session_duration_seconds',
    'Session duration in seconds',
    ['status']
)

token_usage = Counter(
    'token_usage_total',
    'Total tokens used',
    ['model', 'type']  # type: input or output
)

budget_exceeded = Counter(
    'budget_exceeded_total',
    'Number of times budget was exceeded',
    ['session_id']
)


class CostProtectionConfig(BaseModel):
    """Cost protection configuration with validation"""
    enabled: bool = Field(
        default=True,
        description="Enable/disable cost protection"
    )
    max_session_cost_usd: float = Field(
        default=5.0,
        description="Maximum cost per session in USD"
    )
    max_session_duration_minutes: int = Field(
        default=30,
        description="Maximum session duration in minutes"
    )
    max_total_cost_per_hour_usd: float = Field(
        default=50.0,
        description="Maximum total cost per hour across all sessions"
    )
    circuit_breaker_threshold_usd: float = Field(
        default=100.0,
        description="Total cost threshold to trigger circuit breaker"
    )
    circuit_breaker_reset_minutes: int = Field(
        default=60,
        description="Minutes to wait before resetting circuit breaker"
    )
    redis_url: Optional[str] = Field(
        default=None,
        description="Redis URL for distributed cost tracking"
    )

    # Token cost estimation (USD per 1M tokens)
    gpt5_input_cost_per_1m: float = Field(default=10.0)
    gpt5_output_cost_per_1m: float = Field(default=30.0)
    gpt5_mini_input_cost_per_1m: float = Field(default=3.0)
    gpt5_mini_output_cost_per_1m: float = Field(default=15.0)
    gpt_realtime_input_cost_per_1m: float = Field(default=100.0)
    gpt_realtime_output_cost_per_1m: float = Field(default=200.0)

    class Config:
        env_prefix = "COST_PROTECTION_"


def get_cost_config() -> CostProtectionConfig:
    """Load cost protection configuration from environment"""
    return CostProtectionConfig(
        enabled=os.getenv("COST_PROTECTION_ENABLED", "true").lower() == "true",
        max_session_cost_usd=float(os.getenv("COST_PROTECTION_MAX_SESSION_COST_USD", "5.0")),
        max_session_duration_minutes=int(os.getenv("COST_PROTECTION_MAX_SESSION_DURATION_MINUTES", "30")),
        max_total_cost_per_hour_usd=float(os.getenv("COST_PROTECTION_MAX_TOTAL_COST_PER_HOUR_USD", "50.0")),
        circuit_breaker_threshold_usd=float(os.getenv("COST_PROTECTION_CIRCUIT_BREAKER_THRESHOLD_USD", "100.0")),
        redis_url=os.getenv("REDIS_URL")
    )


class SessionCostTracker:
    """
    Track API costs per session with budget enforcement
    Supports both in-memory and Redis-backed storage
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis_client = redis_client
        self.config = get_cost_config()

        # In-memory storage (fallback or single instance)
        self.session_costs: Dict[str, float] = {}
        self.session_tokens: Dict[str, Dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})
        self.session_start_times: Dict[str, datetime] = {}

        # Circuit breaker state
        self.circuit_breaker_active = False
        self.circuit_breaker_reset_time: Optional[datetime] = None
        self.total_cost_last_hour = 0.0

        logger.info("SessionCostTracker initialized")

    async def start_session(self, session_id: str):
        """Initialize cost tracking for a new session"""
        self.session_start_times[session_id] = datetime.utcnow()
        self.session_costs[session_id] = 0.0
        active_sessions.inc()

        if self.redis_client:
            try:
                await self.redis_client.hset(
                    f"session:{session_id}",
                    mapping={
                        "start_time": self.session_start_times[session_id].isoformat(),
                        "cost": "0.0",
                        "input_tokens": "0",
                        "output_tokens": "0"
                    }
                )
                await self.redis_client.expire(
                    f"session:{session_id}",
                    self.config.max_session_duration_minutes * 60 + 300  # Add 5 min buffer
                )
            except Exception as e:
                logger.error(f"Failed to store session in Redis: {e}")

        logger.info(f"Session started: {session_id}")

    async def end_session(self, session_id: str):
        """End session and record metrics"""
        if session_id in self.session_start_times:
            duration = (datetime.utcnow() - self.session_start_times[session_id]).total_seconds()
            session_duration.labels(status="completed").observe(duration)

            # Cleanup
            active_sessions.dec()
            self.session_start_times.pop(session_id, None)
            self.session_costs.pop(session_id, None)
            self.session_tokens.pop(session_id, None)

            if self.redis_client:
                try:
                    await self.redis_client.delete(f"session:{session_id}")
                except Exception as e:
                    logger.error(f"Failed to delete session from Redis: {e}")

            logger.info(f"Session ended: {session_id}, duration: {duration}s")

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate cost for API call based on token usage

        Args:
            model: Model name (gpt-5, gpt-5-mini, gpt-realtime)
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Cost in USD
        """
        config = self.config

        # Determine pricing based on model
        if model == "gpt-5":
            input_cost_per_1m = config.gpt5_input_cost_per_1m
            output_cost_per_1m = config.gpt5_output_cost_per_1m
        elif model == "gpt-5-mini":
            input_cost_per_1m = config.gpt5_mini_input_cost_per_1m
            output_cost_per_1m = config.gpt5_mini_output_cost_per_1m
        elif model == "gpt-realtime":
            input_cost_per_1m = config.gpt_realtime_input_cost_per_1m
            output_cost_per_1m = config.gpt_realtime_output_cost_per_1m
        else:
            logger.warning(f"Unknown model {model}, using gpt-5-mini pricing")
            input_cost_per_1m = config.gpt5_mini_input_cost_per_1m
            output_cost_per_1m = config.gpt5_mini_output_cost_per_1m

        # Calculate cost
        input_cost = (input_tokens / 1_000_000) * input_cost_per_1m
        output_cost = (output_tokens / 1_000_000) * output_cost_per_1m
        total_cost = input_cost + output_cost

        logger.debug(
            f"Cost calculation: {model} - "
            f"input_tokens={input_tokens}, output_tokens={output_tokens}, "
            f"cost=${total_cost:.6f}"
        )

        return total_cost

    async def track_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> Dict[str, any]:
        """
        Track token usage and costs for a session

        Returns:
            Dict with usage stats and budget status
        """
        # Calculate cost for this call
        cost = self.calculate_cost(model, input_tokens, output_tokens)

        # Update session cost
        current_cost = self.session_costs.get(session_id, 0.0)
        new_cost = current_cost + cost
        self.session_costs[session_id] = new_cost

        # Update token counters
        self.session_tokens[session_id]["input"] += input_tokens
        self.session_tokens[session_id]["output"] += output_tokens

        # Update Prometheus metrics
        api_cost_total.labels(model=model, session_id=session_id).inc(cost)
        token_usage.labels(model=model, type="input").inc(input_tokens)
        token_usage.labels(model=model, type="output").inc(output_tokens)

        # Update Redis if available
        if self.redis_client:
            try:
                await self.redis_client.hset(
                    f"session:{session_id}",
                    mapping={
                        "cost": str(new_cost),
                        "input_tokens": str(self.session_tokens[session_id]["input"]),
                        "output_tokens": str(self.session_tokens[session_id]["output"])
                    }
                )
            except Exception as e:
                logger.error(f"Failed to update session in Redis: {e}")

        # Check budget limits
        budget_status = await self.check_budget(session_id, new_cost)

        logger.info(
            f"Usage tracked - session:{session_id}, model:{model}, "
            f"tokens:{input_tokens}in/{output_tokens}out, "
            f"cost:${cost:.6f}, total:${new_cost:.6f}, "
            f"budget_ok:{budget_status['budget_ok']}"
        )

        return {
            "session_id": session_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "call_cost": cost,
            "session_cost": new_cost,
            **budget_status
        }

    async def check_budget(self, session_id: str, current_cost: float) -> Dict[str, any]:
        """
        Check if session is within budget limits

        Returns:
            Dict with budget status and remaining budget
        """
        config = self.config

        # Check session cost limit
        budget_ok = current_cost < config.max_session_cost_usd
        remaining_budget = config.max_session_cost_usd - current_cost

        # Check session duration
        if session_id in self.session_start_times:
            duration = (datetime.utcnow() - self.session_start_times[session_id]).total_seconds()
            max_duration_seconds = config.max_session_duration_minutes * 60
            duration_ok = duration < max_duration_seconds
            remaining_duration = max_duration_seconds - duration
        else:
            duration_ok = True
            remaining_duration = config.max_session_duration_minutes * 60

        # Check circuit breaker
        circuit_breaker_ok = not self.circuit_breaker_active

        # Overall status
        overall_ok = budget_ok and duration_ok and circuit_breaker_ok

        if not overall_ok:
            budget_exceeded.labels(session_id=session_id).inc()

        return {
            "budget_ok": overall_ok,
            "remaining_budget_usd": max(0, remaining_budget),
            "remaining_duration_seconds": max(0, remaining_duration),
            "circuit_breaker_active": self.circuit_breaker_active,
            "warnings": self._get_warnings(budget_ok, duration_ok, circuit_breaker_ok)
        }

    def _get_warnings(self, budget_ok: bool, duration_ok: bool, circuit_breaker_ok: bool) -> list:
        """Generate warning messages for budget status"""
        warnings = []

        if not budget_ok:
            warnings.append("Session budget limit exceeded")
        if not duration_ok:
            warnings.append("Session duration limit exceeded")
        if not circuit_breaker_ok:
            warnings.append("System-wide circuit breaker is active")

        return warnings

    async def activate_circuit_breaker(self):
        """Activate circuit breaker to stop all sessions"""
        self.circuit_breaker_active = True
        self.circuit_breaker_reset_time = datetime.utcnow() + timedelta(
            minutes=self.config.circuit_breaker_reset_minutes
        )

        logger.critical(
            f"Circuit breaker activated! Will reset at {self.circuit_breaker_reset_time}"
        )

    async def check_circuit_breaker(self):
        """Check if circuit breaker should be reset"""
        if self.circuit_breaker_active and self.circuit_breaker_reset_time:
            if datetime.utcnow() >= self.circuit_breaker_reset_time:
                self.circuit_breaker_active = False
                self.circuit_breaker_reset_time = None
                logger.info("Circuit breaker reset")


# Global cost tracker instance
_cost_tracker: Optional[SessionCostTracker] = None


def get_cost_tracker() -> SessionCostTracker:
    """Get or create global cost tracker instance"""
    global _cost_tracker

    if _cost_tracker is None:
        config = get_cost_config()
        redis_client = None

        if config.redis_url:
            try:
                redis_client = redis.from_url(
                    config.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5
                )
                logger.info("Cost tracker using Redis for distributed tracking")
            except Exception as e:
                logger.error(f"Failed to initialize Redis for cost tracking: {e}")

        _cost_tracker = SessionCostTracker(redis_client)

    return _cost_tracker


class CostProtectionMiddleware:
    """
    Middleware to enforce cost protection limits
    Intercepts requests and validates budget status
    """

    def __init__(self, app):
        self.app = app
        self.config = get_cost_config()
        self.tracker = get_cost_tracker()

    async def __call__(self, scope, receive, send):
        # Skip cost protection if disabled
        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        # Check circuit breaker
        await self.tracker.check_circuit_breaker()

        if self.tracker.circuit_breaker_active:
            # Circuit breaker is active - reject request
            if scope["type"] == "http":
                response = JSONResponse(
                    status_code=503,
                    content={
                        "error": "Service temporarily unavailable",
                        "message": "System is under high load. Please try again later.",
                        "circuit_breaker_active": True,
                        "reset_time": self.tracker.circuit_breaker_reset_time.isoformat() if self.tracker.circuit_breaker_reset_time else None
                    }
                )
                await response(scope, receive, send)
                return

        # Continue with request
        await self.app(scope, receive, send)
