"""Small structured trace interface; no prompts, credentials or hidden reasoning."""

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, TextIO

from pydantic import Field

from deephelp_app.domain.models import DTO, Identifier

request_context: ContextVar[tuple[str, str] | None] = ContextVar("request_context", default=None)


class TraceEvent(DTO):
    event: Literal[
        "request_received",
        "input_validated",
        "request_finished",
        "request_failed",
        "request_cancelled",
        "text_cleaned",
        "entities_extracted",
        "extraction_layer_finished",
        "text_processing_cancelled",
        "text_processing_failed",
        "stage_finished",
        "message_replayed",
        "ledger_committed",
    ]
    request_id: Identifier
    trace_id: Identifier
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_length: int | None = Field(default=None, ge=0)
    status_code: int | None = None
    error_code: str | None = None
    stage: str | None = None
    layer: Literal["regex", "api", "strong"] | None = None
    elapsed_ms: float | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    entity_count: int | None = Field(default=None, ge=0)
    run_id: Identifier | None = None
    question_id: Identifier | None = None
    tool_calls: int | None = Field(default=None, ge=0)
    stage_status: Literal["completed", "skipped", "disabled", "failed"] | None = None


class TraceSink(Protocol):
    async def emit(self, event: TraceEvent) -> None: ...

    async def aclose(self) -> None: ...


class JsonlTrace:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    def _append(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()

    async def emit(self, event: TraceEvent) -> None:
        async with self._lock:
            task = asyncio.create_task(asyncio.to_thread(self._append, event.model_dump_json()))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A thread cannot be killed; finish the short write before releasing the lock.
                await task
                raise

    async def aclose(self) -> None:
        async with self._lock:
            self._file.close()


class MemoryTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.closed = False

    async def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        self.closed = True
