import asyncio

import httpx
import pytest

from deephelp_app.domain.models import ErrorCode
from deephelp_app.errors import AppError
from deephelp_app.execution import AsyncCalls, ExecutionBudget, read_json
from deephelp_app.fakes import FakeModelGateway

pytestmark = pytest.mark.unit


async def test_actual_timeout_has_exact_retry_count_and_cleans_children():
    calls = AsyncCalls(1, 0.02)
    budget = ExecutionBudget.start(2, 5, 1)
    started = cleaned = 0

    async def blocked():
        nonlocal started, cleaned
        started += 1
        try:
            await asyncio.Event().wait()
        finally:
            cleaned += 1

    with pytest.raises(AppError) as error:
        await calls.call(blocked, budget, retry_safe=True)
    assert error.value.code == ErrorCode.TIMEOUT
    assert started == cleaned == 2
    assert budget.usage().attempts == 2
    assert budget.usage().retries == 1


async def test_layers_share_retry_budget_without_resetting_it():
    budget = ExecutionBudget.start(2, 5, 1)
    first = AsyncCalls(1, 1)
    second = AsyncCalls(1, 1)
    attempts = 0

    async def transient():
        nonlocal attempts
        attempts += 1
        if attempts in {1, 3}:
            raise httpx.ConnectError("synthetic failure")
        return "ok"

    assert await first.call(transient, budget, retry_safe=True) == "ok"
    with pytest.raises(AppError, match="unavailable"):
        await second.call(transient, budget, retry_safe=True)
    assert attempts == 3
    assert budget.retries_used == 1
    assert budget.attempts_used == 3


async def test_unsafe_operation_is_never_automatically_retried():
    budget = ExecutionBudget.start(2, 5, 3)
    calls = AsyncCalls(1, 1)
    attempts = 0

    async def uncertain_write():
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("synthetic lost result")

    with pytest.raises(AppError):
        await calls.call(uncertain_write, budget)
    assert attempts == 1
    assert budget.retries_used == 0


async def test_programming_error_is_not_swallowed_or_retried():
    calls = AsyncCalls(1, 1)
    budget = ExecutionBudget.start(2, 5, 3)

    async def bug():
        raise ValueError("synthetic programming error")

    with pytest.raises(ValueError, match="programming error"):
        await calls.call(bug, budget, retry_safe=True)
    assert budget.attempts_used == 1


async def test_cancelled_task_releases_semaphore_and_runs_cleanup():
    calls = AsyncCalls(1, 1)
    budget = ExecutionBudget.start(2, 5, 1)
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(calls.call(blocked, budget, retry_safe=True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert await calls.call(lambda: asyncio.sleep(0, result="next"), budget) == "next"
    assert budget.retries_used == 0


async def test_deadline_includes_semaphore_queue_wait():
    calls = AsyncCalls(1, 2)
    holder_started = asyncio.Event()

    async def holder():
        holder_started.set()
        await asyncio.Event().wait()

    holder_task = asyncio.create_task(calls.call(holder, ExecutionBudget.start(3, 1, 0)))
    await holder_started.wait()
    waiting = ExecutionBudget.start(0.02, 1, 0)
    try:
        with pytest.raises(AppError) as error:
            await calls.call(lambda: asyncio.sleep(0), waiting)
        assert error.value.code == ErrorCode.BUDGET_EXHAUSTED
        assert waiting.attempts_used == 0
    finally:
        holder_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder_task


async def test_attempt_cap_limits_retries_and_expired_budget_starts_no_call():
    calls = AsyncCalls(1, 1)
    budget = ExecutionBudget.start(2, 1, 5)

    async def unavailable():
        raise httpx.ConnectError("synthetic")

    with pytest.raises(AppError):
        await calls.call(unavailable, budget, retry_safe=True)
    assert budget.attempts_used == 1
    assert budget.retries_used == 0
    budget.deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(AppError) as error:
        await calls.call(unavailable, budget)
    assert error.value.code == ErrorCode.BUDGET_EXHAUSTED
    assert budget.attempts_used == 1


async def test_fake_model_concurrency_and_complete_cleanup():
    gateway = FakeModelGateway(AsyncCalls(2, 1), delay=0.02)
    results = await asyncio.gather(
        *[gateway.complete("synthetic", ExecutionBudget.start(2, 1, 0)) for _ in range(6)]
    )
    assert results == ["FAKE_MODEL_OUTPUT"] * 6
    assert gateway.peak_active == 2
    assert gateway.call_count == gateway.cleaned == 6
    assert gateway.active == 0


async def test_retryable_http_status_is_bounded():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError) as error:
            await read_json(
                client,
                "https://synthetic.invalid",
                AsyncCalls(1, 1),
                ExecutionBudget.start(2, 3, 1),
            )
    assert error.value.code == ErrorCode.RATE_LIMITED
    assert attempts == 2
