"""Run all three experiments with python -m deephelp_app.experiments (offline)."""

import asyncio
import json
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Literal


async def io_comparison(delay: float = 0.1) -> dict[str, object]:
    active = peak = 0

    async def io(label: str) -> str:
        nonlocal active, peak
        # sleep represents an independent I/O wait; it releases the event loop.
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(delay)
            return label
        finally:
            active -= 1

    start = time.perf_counter()
    serial = [await io("A"), await io("B")]
    serial_seconds = time.perf_counter() - start
    serial_peak = peak
    peak = 0
    start = time.perf_counter()
    async with asyncio.TaskGroup() as group:
        first = group.create_task(io("A"))
        second = group.create_task(io("B"))
    return {
        "serial_results": serial,
        "concurrent_results": [first.result(), second.result()],
        "serial_peak": serial_peak,
        "concurrent_peak": peak,
        "serial_seconds": serial_seconds,
        "concurrent_seconds": time.perf_counter() - start,
    }


async def cancellation_experiment(failure: Literal["timeout", "error"] = "timeout") -> list[str]:
    sibling_started = asyncio.Event()
    events: list[str] = []

    async def sibling() -> None:
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("sibling_cancelled")
            raise
        finally:
            # Real adapters close streams/transactions in this position.
            events.append("sibling_cleaned")

    async def failing() -> None:
        await sibling_started.wait()
        if failure == "error":
            raise ValueError("synthetic child failure")
        async with asyncio.timeout(0.02):
            await asyncio.Event().wait()

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(sibling())
            group.create_task(failing())
    except* TimeoutError, ValueError:
        # The demo records the expected failure. Production must map it to an explicit error.
        events.append("group_failed")
    events.append("all_tasks_joined")
    return events


def cpu_work(duration: float = 0.15) -> int:
    """Deliberately holds the current thread; deterministic loop until a wall-time deadline."""
    end = time.perf_counter() + duration
    value = 0
    while time.perf_counter() < end:
        value = (value * 31 + 7) % 1000003
    return value


async def cpu_comparison(duration: float = 0.15) -> dict[str, int]:
    async def measure(offload: bool) -> int:
        ticks = 0
        running = True

        async def heartbeat() -> None:
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0.005)

        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)  # Ensure heartbeat starts before the CPU job.
        before = ticks
        try:
            if offload:
                # Threads still share the GIL. A process is the replaceable pure-Python CPU seam.
                pool = ProcessPoolExecutor(max_workers=1)
                try:
                    await asyncio.get_running_loop().run_in_executor(pool, cpu_work, duration)
                finally:
                    # Cancellation cannot kill running CPU work; join outside the event loop.
                    await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=True)
            else:
                cpu_work(duration)  # Intentionally bad: blocks this event-loop thread.
            return ticks - before
        finally:
            running = False
            await task

    return {
        "blocking_heartbeat_ticks": await measure(False),
        "process_heartbeat_ticks": await measure(True),
    }


async def main() -> None:
    print(
        json.dumps(
            {
                "io": await io_comparison(),
                "timeout": await cancellation_experiment("timeout"),
                "failure": await cancellation_experiment("error"),
                "cpu": await cpu_comparison(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    # Windows process spawning must be guarded; import does not launch a pool.
    asyncio.run(main())
