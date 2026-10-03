"""Small structured trace interface; no prompts, credentials or hidden reasoning."""

import asyncio
import json
import secrets
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from deephelp_app.domain.models import DTO, DebugSnapshot, Identifier

request_context: ContextVar[tuple[str, str] | None] = ContextVar("request_context", default=None)
sop_node_observer: ContextVar[Callable[[dict[str, object]], None] | None] = ContextVar(
    "sop_node_observer", default=None
)


@contextmanager
def observe_sop_node(node_id: str, kind: str, calls: list[str]) -> Iterator[dict[str, object]]:
    observation: dict[str, object] = {
        "node_id": node_id,
        "kind": kind,
        "status": "completed",
        "started_at": datetime.now(UTC).isoformat(),
    }
    before = len(calls)
    try:
        yield observation
    except BaseException as exc:
        observation["status"] = "failed"
        observation["error_code"] = str(
            getattr(
                exc,
                "code",
                "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "INTERNAL_ERROR",
            )
        )
        raise
    finally:
        observation["finished_at"] = datetime.now(UTC).isoformat()
        observation["tool_call_ids"] = calls[before:]
        if observer := sop_node_observer.get():
            observer(observation)


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
        "debug_snapshot",
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
    intent_calls: int | None = Field(default=None, ge=0)
    retrieval_calls: int | None = Field(default=None, ge=0)
    entity_count: int | None = Field(default=None, ge=0)
    run_id: Identifier | None = None
    question_id: Identifier | None = None
    tool_calls: int | None = Field(default=None, ge=0)
    stage_status: Literal["completed", "skipped", "disabled", "failed"] | None = None
    debug: DebugSnapshot | None = None


class TraceSink(Protocol):
    async def emit(self, event: TraceEvent) -> None: ...

    async def aclose(self) -> None: ...


class JsonlTrace:
    """Bounded best-effort diagnostics. MySQL remains the business audit source."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        backups: int = 3,
        retention_seconds: float = 7 * 86400,
        queue_size: int = 128,
    ) -> None:
        if min(max_bytes, backups, retention_seconds, queue_size) <= 0:
            raise ValueError("Trace limits must be positive")
        self.path, self.max_bytes, self.backups = path, max_bytes, backups
        self.retention_seconds = retention_seconds
        self.dropped = 0
        self.debug_key = secrets.token_bytes(32)
        self._recent: deque[TraceEvent] = deque(maxlen=queue_size)
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None
        self.closed = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key_path = path.with_suffix(path.suffix + ".key")
            try:
                with key_path.open("xb") as file:
                    file.write(self.debug_key)
            except FileExistsError:
                self.debug_key = key_path.read_bytes()
            if len(self.debug_key) != 32:
                raise OSError("Invalid trace redaction key")
        except OSError:
            self.dropped += 1

    def _paths(self) -> list[Path]:
        return [self.path, *(Path(str(self.path) + f".{i}") for i in range(1, self.backups + 1))]

    def _expire(self) -> None:
        now = datetime.now(UTC).timestamp()
        for path in self._paths():
            if path.exists():
                oldest = path.stat().st_mtime
                with path.open("rb") as file:
                    try:
                        row = json.loads(file.readline(min(self.max_bytes, 256 * 1024)))
                        oldest = min(oldest, datetime.fromisoformat(row["at"]).timestamp())
                    except ValueError, KeyError, TypeError:
                        pass
                if now - oldest > self.retention_seconds:
                    path.unlink()

    def _append(self, line: str) -> None:
        self._expire()
        if (
            self.path.exists()
            and self.path.stat().st_size + len(line.encode()) + 1 > self.max_bytes
        ):
            paths = self._paths()
            paths[-1].unlink(missing_ok=True)
            for index in range(len(paths) - 2, -1, -1):
                if paths[index].exists():
                    paths[index].replace(paths[index + 1])
        with self.path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    async def _drain(self) -> None:
        while True:
            line = await self._queue.get()
            try:
                if line is None:
                    return
                try:
                    await asyncio.to_thread(self._append, line)
                except OSError:
                    self.dropped += 1
            finally:
                self._queue.task_done()

    async def emit(self, event: TraceEvent) -> None:
        if self.closed:
            self.dropped += 1
            return
        line = event.model_dump_json()
        if len(line.encode()) + 1 > self.max_bytes:
            self.dropped += 1
            return
        if self._worker is None:
            self._worker = asyncio.create_task(self._drain())
        self._recent.append(event)
        try:
            self._queue.put_nowait(line)
        except asyncio.QueueFull:
            self.dropped += 1

    def _read(self, scope: str, run_id: str) -> DebugSnapshot | None:
        now = datetime.now(UTC)
        for event in reversed(tuple(self._recent)):
            if (now - event.at).total_seconds() <= self.retention_seconds and event.debug:
                if event.debug.scope_hash == scope and event.debug.run_id == run_id:
                    return event.debug
        for path in self._paths():
            try:
                if now.timestamp() - path.stat().st_mtime > self.retention_seconds:
                    continue
                # Never scan an unbounded legacy file.
                with path.open("rb") as file:
                    size = path.stat().st_size
                    if size > self.max_bytes:
                        file.seek(size - self.max_bytes)
                        file.readline()
                    lines = file.read(self.max_bytes).splitlines()
                for line in reversed(lines):
                    try:
                        raw = json.loads(line)
                        if raw.get("event") != "debug_snapshot":
                            continue
                        event = TraceEvent.model_validate(raw)
                        if (now - event.at).total_seconds() > self.retention_seconds:
                            continue
                        if (
                            event.debug
                            and event.debug.scope_hash == scope
                            and event.debug.run_id == run_id
                        ):
                            return event.debug
                    except ValueError, AttributeError, TypeError:
                        continue
            except OSError:
                continue
        return None

    async def read_debug(self, scope: str, run_id: str) -> DebugSnapshot | None:
        return await asyncio.to_thread(self._read, scope, run_id)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._worker:
            await self._queue.put(None)
            await self._worker


class MemoryTrace:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.closed = False
        self.debug_key = secrets.token_bytes(32)
        self.dropped = 0

    async def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        self.closed = True

    async def read_debug(self, scope: str, run_id: str) -> DebugSnapshot | None:
        for event in reversed(self.events):
            if event.debug and event.debug.scope_hash == scope and event.debug.run_id == run_id:
                return event.debug
        return None
