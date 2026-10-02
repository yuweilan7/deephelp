"""One mutable runtime budget per request; do not serialize this object into domain DTOs."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

import httpx

from deephelp_app.domain.models import BudgetSnapshot, BudgetUsed, ErrorCode
from deephelp_app.errors import AppError


@dataclass
class ExecutionBudget:
    deadline: float
    remaining_attempts: int
    retry_remaining: int
    attempts_used: int = 0
    retries_used: int = 0
    tool_steps_used: int = 0
    token_limit: int | None = None
    cost_limit: Decimal | None = None
    tokens_used: int = 0
    cost_used: Decimal = Decimal("0")
    token_upper_bound: int = 0
    cost_upper_bound: Decimal = Decimal("0")
    uncertain_attempts: int = 0
    on_change: Callable[[], None] | None = None

    @classmethod
    def start(
        cls,
        timeout: float,
        attempts: int,
        retries: int,
        *,
        token_limit: int | None = None,
        cost_limit: Decimal | None = None,
    ) -> ExecutionBudget:
        if timeout <= 0 or attempts < 1 or retries < 0:
            raise ValueError("Budget limits must be positive (retries may be zero)")
        if token_limit is not None and token_limit < 1:
            raise ValueError("Token limit must be positive")
        if cost_limit is not None and (not cost_limit.is_finite() or cost_limit <= 0):
            raise ValueError("Cost limit must be finite and positive")
        return cls(
            asyncio.get_running_loop().time() + timeout,
            attempts,
            retries,
            token_limit=token_limit,
            cost_limit=cost_limit,
        )

    def remaining_seconds(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Request deadline exhausted", 504)
        return remaining

    def claim_attempt(
        self,
        *,
        retry: bool,
        tokens: int = 0,
        cost: Decimal = Decimal("0"),
    ) -> None:
        # No await between the check and decrement: concurrent tasks share the same counters.
        self.remaining_seconds()
        if self.remaining_attempts <= 0 or (retry and self.retry_remaining <= 0):
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Request call budget exhausted", 503)
        if tokens < 0 or not cost.is_finite() or cost < 0:
            raise ValueError("Invalid model reservation")
        if (
            self.token_limit is not None and self.token_upper_bound + tokens > self.token_limit
        ) or (self.cost_limit is not None and self.cost_upper_bound + cost > self.cost_limit):
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Model token or cost budget exhausted")
        self.remaining_attempts -= 1
        self.attempts_used += 1
        self.token_upper_bound += tokens
        self.cost_upper_bound += cost
        if tokens:
            self.uncertain_attempts += 1
        if retry:
            self.retry_remaining -= 1
            self.retries_used += 1
        if self.on_change:
            self.on_change()

    def settle_model(
        self, reserved_tokens: int, reserved_cost: Decimal, tokens: int, cost: Decimal
    ) -> None:
        """Unknown/time-out attempts retain the reservation; only valid usage settles it."""
        self.tokens_used += tokens
        self.cost_used += cost
        self.token_upper_bound += tokens - reserved_tokens
        self.cost_upper_bound += cost - reserved_cost
        self.uncertain_attempts -= 1
        if self.on_change:
            self.on_change()
        if (self.token_limit is not None and self.token_upper_bound > self.token_limit) or (
            self.cost_limit is not None and self.cost_upper_bound > self.cost_limit
        ):
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Provider usage exceeded reserved budget")

    def usage(self) -> BudgetUsed:
        return BudgetUsed(
            attempts=self.attempts_used,
            retries=self.retries_used,
            tool_steps=self.tool_steps_used,
            tokens=self.tokens_used if self.token_limit is not None else None,
            cost=self.cost_used if self.cost_limit is not None else None,
            token_upper_bound=self.token_upper_bound if self.token_limit is not None else None,
            cost_upper_bound=self.cost_upper_bound if self.cost_limit is not None else None,
            uncertain_attempts=self.uncertain_attempts,
        )

    def snapshot(self) -> BudgetSnapshot:
        remaining = max(0.0, self.deadline - asyncio.get_running_loop().time())
        return BudgetSnapshot(
            remaining_seconds=remaining,
            remaining_attempts=self.remaining_attempts,
            retry_remaining=self.retry_remaining,
            used=self.usage(),
            token_limit=self.token_limit,
            cost_limit=self.cost_limit,
            stop_reason=(
                ErrorCode.BUDGET_EXHAUSTED
                if (
                    remaining == 0
                    or self.remaining_attempts == 0
                    or (self.token_limit is not None and self.token_upper_bound >= self.token_limit)
                    or (self.cost_limit is not None and self.cost_upper_bound >= self.cost_limit)
                )
                else None
            ),
        )


class AsyncCalls:
    def __init__(self, concurrency: int, child_timeout: float, *, backoff: float = 0.05) -> None:
        if concurrency < 1 or child_timeout <= 0:
            raise ValueError("Invalid concurrency or child timeout")
        self._slots = asyncio.Semaphore(concurrency)
        self.child_timeout = child_timeout
        if backoff < 0:
            raise ValueError("Backoff must be nonnegative")
        self.backoff = backoff

    async def call[T](
        self,
        operation: Callable[[], Awaitable[T]],
        budget: ExecutionBudget,
        *,
        retry_safe: bool = False,
        token_reservation: int = 0,
        cost_reservation: Decimal = Decimal("0"),
    ) -> T:
        retry = False
        while True:
            # Waiting for the semaphore is included in the original request deadline.
            try:
                async with asyncio.timeout(budget.remaining_seconds()):
                    async with self._slots:
                        budget.claim_attempt(
                            retry=retry, tokens=token_reservation, cost=cost_reservation
                        )
                        try:
                            async with asyncio.timeout(
                                min(self.child_timeout, budget.remaining_seconds())
                            ):
                                return await operation()
                        except TimeoutError, httpx.TimeoutException:
                            error = AppError(
                                ErrorCode.TIMEOUT, "Subcall timed out", 504, retryable=True
                            )
                        except httpx.TransportError:
                            error = AppError(
                                ErrorCode.UPSTREAM_UNAVAILABLE,
                                "Read upstream unavailable",
                                503,
                                retryable=True,
                            )
                        except AppError as exc:
                            if not exc.retryable:
                                raise
                            error = exc
            except TimeoutError:
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED, "Request deadline exhausted", 504
                ) from None
            # CancelledError and unexpected programming exceptions deliberately propagate.
            if not retry_safe:
                raise error
            budget.remaining_seconds()
            if budget.retry_remaining <= 0 or budget.remaining_attempts <= 0:
                raise error
            delay = min(1.0, self.backoff * 2 ** min(budget.retries_used, 8))
            delay += random.uniform(0, self.backoff / 2)
            try:
                async with asyncio.timeout(budget.remaining_seconds()):
                    await asyncio.sleep(delay)
            except TimeoutError:
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED, "Request deadline exhausted", 504
                ) from None
            retry = True


async def read_json(
    client: httpx.AsyncClient, url: str, calls: AsyncCalls, budget: ExecutionBudget
) -> object:
    """Read-only transport seam. Stream context releases the pool lease on cancellation."""

    async def operation() -> object:
        async with client.stream("GET", url) as response:
            if response.status_code in {429, 502, 503, 504}:
                code = (
                    ErrorCode.RATE_LIMITED
                    if response.status_code == 429
                    else ErrorCode.UPSTREAM_UNAVAILABLE
                )
                raise AppError(code, "Read upstream unavailable", 503, retryable=True)
            response.raise_for_status()
            await response.aread()
            return response.json()

    return await calls.call(operation, budget, retry_safe=True)
