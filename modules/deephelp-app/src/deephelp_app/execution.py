"""One mutable runtime budget per request; do not serialize this object into domain DTOs."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from deephelp_app.domain.models import BudgetUsed, ErrorCode
from deephelp_app.errors import AppError


@dataclass
class ExecutionBudget:
    deadline: float
    remaining_attempts: int
    retry_remaining: int
    attempts_used: int = 0
    retries_used: int = 0

    @classmethod
    def start(cls, timeout: float, attempts: int, retries: int) -> ExecutionBudget:
        if timeout <= 0 or attempts < 1 or retries < 0:
            raise ValueError("Budget limits must be positive (retries may be zero)")
        return cls(asyncio.get_running_loop().time() + timeout, attempts, retries)

    def remaining_seconds(self) -> float:
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Request deadline exhausted", 504)
        return remaining

    def claim_attempt(self, *, retry: bool) -> None:
        # No await between the check and decrement: concurrent tasks share the same counters.
        self.remaining_seconds()
        if self.remaining_attempts <= 0 or (retry and self.retry_remaining <= 0):
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Request call budget exhausted", 503)
        self.remaining_attempts -= 1
        self.attempts_used += 1
        if retry:
            self.retry_remaining -= 1
            self.retries_used += 1

    def usage(self) -> BudgetUsed:
        return BudgetUsed(attempts=self.attempts_used, retries=self.retries_used)


class AsyncCalls:
    def __init__(self, concurrency: int, child_timeout: float) -> None:
        if concurrency < 1 or child_timeout <= 0:
            raise ValueError("Invalid concurrency or child timeout")
        self._slots = asyncio.Semaphore(concurrency)
        self.child_timeout = child_timeout

    async def call[T](
        self,
        operation: Callable[[], Awaitable[T]],
        budget: ExecutionBudget,
        *,
        retry_safe: bool = False,
    ) -> T:
        retry = False
        while True:
            # Waiting for the semaphore is included in the original request deadline.
            try:
                async with asyncio.timeout(budget.remaining_seconds()):
                    async with self._slots:
                        budget.claim_attempt(retry=retry)
                        try:
                            async with asyncio.timeout(
                                min(self.child_timeout, budget.remaining_seconds())
                            ):
                                return await operation()
                        except TimeoutError, httpx.TimeoutException:
                            error = AppError(
                                ErrorCode.TIMEOUT, "Subcall timed out", 504, retryable=True
                            )
                        except httpx.ConnectError, httpx.ReadError:
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
