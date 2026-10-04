"""Single-process, durable, bounded learning jobs; evidence is separate from state."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.application.dense import atomic_json
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import ErrorCode

Runner = Callable[[dict[str, Any], Path], Awaitable[dict[str, Any]]]


class LearningJobs:
    def __init__(self, root: Path, runner: Runner, *, timeout: float = 1800):
        self.root, self.runner, self.timeout = root, runner, timeout
        root.mkdir(parents=True, exist_ok=True)
        self.rows: dict[str, dict[str, Any]] = {}
        self.worker: asyncio.Task[None] | None = None
        self.current: str | None = None
        self.guard = asyncio.Lock()
        for path in root.glob("*.json"):
            row = json.loads(path.read_text(encoding="utf-8"))
            if path.stem != row["id"]:
                raise ValueError("Job identity differs from durable file")
            if row["state"] in {"RUNNING", "CANCELLING"}:
                row["state"] = "INTERRUPTED"
                row["error"] = "Previous process ended; explicit recovery required"
                self.save(row)
            self.rows[row["id"]] = row

    def save(self, row: dict[str, Any]) -> None:
        row["updated_at"] = datetime.now(UTC).isoformat()
        atomic_json(self.root / (row["id"] + ".json"), row)

    def get(self, identity: str) -> dict[str, Any]:
        if identity not in self.rows:
            raise AppError(ErrorCode.NOT_FOUND, "Learning task not found", 404)
        return self.rows[identity]

    def available(self) -> None:
        if self.worker is not None and not self.worker.done():
            raise AppError(ErrorCode.VERSION_CONFLICT, "One heavy task at a time", 409)

    async def start(self, options: dict[str, Any], *, resume: str | None = None) -> dict[str, Any]:
        async with self.guard:
            self.available()
            if resume:
                row = self.get(resume)
                if row["state"] not in {"CANCELLED", "INTERRUPTED", "FAILED"}:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Task is not recoverable", 409)
                if row["attempt"] >= 3:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Three task attempts exhausted", 409)
                row["history"].append(
                    {k: row.get(k) for k in ("attempt", "state", "error", "result")}
                )
                row["attempt"] += 1
            else:
                if len(self.rows) >= 100:
                    raise AppError(
                        ErrorCode.BUDGET_EXHAUSTED, "Learning task capacity reached", 409
                    )
                row = dict(
                    id=uuid4().hex,
                    options=options,
                    attempt=1,
                    history=[],
                    created_at=datetime.now(UTC).isoformat(),
                )
                self.rows[row["id"]] = row
            row.update(state="RUNNING", result=None, error=None, diagnostic=None)
            self.save(row)
            self.current = row["id"]
            self.worker = asyncio.create_task(self.execute(row))
            return row

    def output(self, row: dict[str, Any]) -> Path:
        return self.root / str(row["id"]) / ("attempt-" + str(row["attempt"]))

    async def execute(self, row: dict[str, Any]) -> None:
        try:
            async with asyncio.timeout(self.timeout):
                row["result"] = await self.runner(row, self.output(row))
            row["state"] = "SUCCEEDED"
        except asyncio.CancelledError:
            row.update(state="CANCELLED", error="Cancelled; partial evidence retained")
        except Exception as exc:
            # SDK errors may contain addresses/credentials. Persist only the type here;
            # original runner reports retain bounded diagnostic detail for local recovery.
            row.update(state="FAILED", error=type(exc).__name__)
            if isinstance(exc, AppError):
                row["diagnostic"] = dict(
                    code=exc.code.value,
                    message=exc.safe_message,
                    request_id=exc.provider_request_id,
                )
            elif isinstance(exc, ConfigurationError):
                row["diagnostic"] = dict(message=str(exc))
        finally:
            self.save(row)

    async def cancel(self, identity: str) -> dict[str, Any]:
        async with self.guard:
            row = self.get(identity)
            if self.current != identity or self.worker is None or self.worker.done():
                return row
            if row["state"] != "CANCELLING":
                row["state"] = "CANCELLING"
                self.save(row)
                self.worker.cancel()
            try:
                await asyncio.shield(self.worker)
            except asyncio.CancelledError:
                if not self.worker.done():
                    raise
                # Cancellation may arrive before execute() enters its try/finally.
                row.update(state="CANCELLED", error="Cancelled before execution")
                self.save(row)
            return row

    async def close(self) -> None:
        if self.current:
            await self.cancel(self.current)
