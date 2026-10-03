"""Feedback facts in MySQL; M10-style short leases never cover external transactions."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.errors import ConfigurationError
from deephelp_app.flywheel import (
    FeedbackCandidate,
    Route,
    RouteReview,
    capture_candidate,
    reviewed,
    withdrawn,
)
from deephelp_app.ledger import MySQLLedger


class FeedbackStore(MySQLLedger):
    async def fresh_rows(self, query: str, values: tuple[Any, ...]) -> list[Any]:
        # A pooled REPEATABLE READ connection may retain a prior SELECT snapshot.
        async with self.pool.acquire() as conn:
            await conn.rollback()
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, values)
                    return list(await cursor.fetchall())
            finally:
                await conn.rollback()

    async def migrate(self) -> None:
        source = Path(__file__).parent / "migrations/018_flywheel.sql"
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in source.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            # Keep early local demonstration rows when upgrading their lineage index.
            await cursor.execute("SHOW INDEX FROM dh_m18_derivatives WHERE Key_name='PRIMARY'")
            if len(await cursor.fetchall()) == 4:
                await cursor.execute(
                    "ALTER TABLE dh_m18_derivatives DROP PRIMARY KEY, ADD PRIMARY KEY "
                    "(candidate_id,revision,route,artifact_sha256,artifact_path)"
                )
            await conn.commit()

    async def capture(
        self, run_id: str, key: bytes, source: str, variant: str
    ) -> FeedbackCandidate:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await conn.rollback()
            await cursor.execute(
                "SELECT m.body,r.response FROM dh_m08_messages m JOIN dh_m08_runs r "
                "ON r.run_id=m.run_id WHERE r.run_id=%s AND r.status<>'RUNNING' AND "
                "r.response IS NOT NULL",
                (run_id,),
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise ConfigurationError("Capture requires a durable completed source run")
            candidate = capture_candidate(
                run_id,
                json.loads(raw[0]),
                json.loads(raw[1]),
                key,
                source_group=source,
                variant_group=variant,
            )
            await cursor.execute(
                "INSERT IGNORE INTO dh_m18_candidates VALUES (%s,%s,0,%s)",
                (candidate.candidate_id, run_id, candidate.model_dump_json()),
            )
            await conn.commit()
        stored = await self.get(candidate.candidate_id)
        if any(
            getattr(stored, k) != getattr(candidate, k)
            for k in ("text", "source_group", "variant_group", "observation")
        ):
            raise ConfigurationError("Same source run has conflicting capture metadata")
        return stored

    async def get(self, candidate_id: str) -> FeedbackCandidate:
        rows = await self.fresh_rows(
            "SELECT body FROM dh_m18_candidates WHERE candidate_id=%s", (candidate_id,)
        )
        if not rows:
            raise ConfigurationError("Feedback candidate not found")
        return FeedbackCandidate.model_validate_json(rows[0][0])

    async def get_many(self, ids: list[str]) -> list[FeedbackCandidate]:
        if not ids or len(ids) > 100 or len(ids) != len(set(ids)):
            raise ConfigurationError("Select 1..100 distinct candidate IDs")
        return [await self.get(key) for key in sorted(ids)]

    async def review(
        self,
        candidate_id: str,
        expected: int,
        route: Route | None,
        review: RouteReview | None,
        *,
        reviewer: str = "",
        reason: str = "",
    ) -> FeedbackCandidate:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT body FROM dh_m18_candidates WHERE candidate_id=%s FOR UPDATE",
                        (candidate_id,),
                    )
                    raw = await cursor.fetchone()
                    if raw is None:
                        raise ConfigurationError("Candidate not found")
                    old = FeedbackCandidate.model_validate_json(raw[0])
                    if old.revision != expected:
                        raise ConfigurationError(
                            "Review revision changed; read and resolve the conflict"
                        )
                    new = (
                        reviewed(old, route, review)
                        if route and review
                        else withdrawn(old, reviewer, reason)
                    )
                    await cursor.execute(
                        "UPDATE dh_m18_candidates SET revision=%s,body=%s WHERE "
                        "candidate_id=%s AND "
                        "revision=%s",
                        (new.revision, new.model_dump_json(), candidate_id, expected),
                    )
                    if cursor.rowcount != 1:
                        raise ConfigurationError("Review CAS lost")
                    await cursor.execute(
                        "INSERT INTO dh_m18_reviews(candidate_id,revision,body) VALUES (%s,%s,%s)",
                        (candidate_id, new.revision, new.model_dump_json()),
                    )
                    await cursor.execute(
                        "UPDATE dh_m18_tasks SET state=%s,lease_token=NULL,lease_until=NULL "
                        "WHERE candidate_id=%s AND state<>'DONE'",
                        ("CANCELLED", candidate_id),
                    )
                    for key, approval in new.reviews.items():
                        if approval.state == "approved":
                            await cursor.execute(
                                "INSERT INTO dh_m18_tasks(candidate_id,revision,route) "
                                "VALUES (%s,%s,%s)",
                                (candidate_id, new.revision, key),
                            )
                await conn.commit()
                return new
            except BaseException:
                await conn.rollback()
                raise

    async def require_current(self, source: dict[str, Any]) -> None:
        for key, raw in source["candidates"].items():
            current = await self.get(key)
            if current.model_dump(mode="json") != raw:
                raise ConfigurationError(
                    "Reviewed source changed; rebuild, never publish stale derivatives"
                )

    async def claim(self, rows: list[FeedbackCandidate]) -> tuple[str, list[int]]:
        token, jobs = uuid4().hex, []
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    for candidate in rows:
                        for route, review in candidate.reviews.items():
                            if review.state != "approved":
                                continue
                            await cursor.execute(
                                "SELECT job_id,state,attempts,lease_until FROM dh_m18_tasks WHERE "
                                "candidate_id=%s AND revision=%s AND route=%s FOR UPDATE",
                                (candidate.candidate_id, candidate.revision, route),
                            )
                            row = await cursor.fetchone()
                            if row is None or row[2] >= 3:
                                raise ConfigurationError(
                                    "Review task missing or bounded attempts exhausted"
                                )
                            await cursor.execute(
                                "UPDATE dh_m18_tasks SET "
                                "state='RUNNING',attempts=attempts+1,lease_token=%s,"
                                "lease_until=DATE_ADD(CURRENT_TIMESTAMP(6),INTERVAL "
                                "600 SECOND) WHERE job_id=%s AND (lease_until IS NULL OR "
                                "lease_until<CURRENT_TIMESTAMP(6))",
                                (token, row[0]),
                            )
                            if cursor.rowcount != 1:
                                raise ConfigurationError(
                                    "Feedback build task is leased by another worker"
                                )
                            jobs.append(int(row[0]))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        return token, jobs

    async def complete(
        self, token: str, jobs: list[int], derivatives: list[dict[str, Any]]
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    for job in jobs:
                        await cursor.execute(
                            "UPDATE dh_m18_tasks SET state=%s,lease_token=NULL,lease_until=NULL "
                            "WHERE job_id=%s AND lease_token=%s "
                            "AND lease_until>CURRENT_TIMESTAMP(6)",
                            ("DONE", job, token),
                        )
                        if cursor.rowcount != 1:
                            raise ConfigurationError(
                                "Feedback task ownership expired or source changed"
                            )
                    for item in derivatives:
                        await cursor.execute(
                            "INSERT IGNORE INTO dh_m18_derivatives VALUES (%s,%s,%s,%s,%s)",
                            tuple(
                                item[k]
                                for k in (
                                    "candidate_id",
                                    "revision",
                                    "route",
                                    "artifact_path",
                                    "artifact_sha256",
                                )
                            ),
                        )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def lineage(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = await self.fresh_rows(
            "SELECT revision,route,artifact_path,artifact_sha256 FROM dh_m18_derivatives "
            "WHERE candidate_id=%s ORDER BY revision,route",
            (candidate_id,),
        )
        return [
            dict(zip(("revision", "route", "artifact_path", "artifact_sha256"), row, strict=True))
            for row in rows
        ]

    async def tasks(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = await self.fresh_rows(
            "SELECT revision,route,state,attempts FROM dh_m18_tasks "
            "WHERE candidate_id=%s ORDER BY revision,route",
            (candidate_id,),
        )
        return [
            dict(zip(("revision", "route", "state", "attempts"), row, strict=True)) for row in rows
        ]
