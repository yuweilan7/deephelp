"""MySQL is the sole active-release authority; switches and run pins use short transactions."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from deephelp_app.approval_store import ApprovalRepository
from deephelp_app.domain.models import ErrorCode, ReleaseManifest, ReleasePointer, RequestEnvelope
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.ledger import Receipt
from deephelp_app.release_assets import release_hash, verify_release


class ReleaseMismatch(AppError):
    def __init__(self, expected: str) -> None:
        super().__init__(
            ErrorCode.VERSION_CONFLICT, "Continuation requires its complete release", 409
        )
        self.expected = expected


class ReleaseStore(FeedbackStore):
    async def migrate(self) -> None:
        await super().migrate()
        sql = Path(__file__).parent / "migrations/018_release.sql"
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            await conn.commit()

    async def register(self, manifest: ReleaseManifest) -> str:
        verify_release(manifest)
        identity = release_hash(manifest)
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    await self.check_source(cursor, manifest)
                    await cursor.execute(
                        "INSERT IGNORE INTO dh_m18_releases(release_hash,version,body) "
                        "VALUES (%s,%s,%s)",
                        (identity, manifest.version, manifest.model_dump_json()),
                    )
                    await cursor.execute(
                        "SELECT release_hash,body,state FROM dh_m18_releases "
                        "WHERE version=%s FOR SHARE",
                        (manifest.version,),
                    )
                    row = await cursor.fetchone()
                    if (
                        not row
                        or row[0] != identity
                        or row[2] != "READY"
                        or (ReleaseManifest.model_validate_json(row[1]) != manifest)
                    ):
                        raise ConfigurationError(
                            "A release version is immutable and cannot be reused"
                        )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        return identity

    async def manifest(self, identity: str, *, ready: bool = True) -> ReleaseManifest:
        rows = await self.fresh_rows(
            "SELECT body,state FROM dh_m18_releases WHERE release_hash=%s", (identity,)
        )
        if not rows or (ready and rows[0][1] != "READY"):
            raise ConfigurationError("Complete release is unavailable or retired")
        manifest = ReleaseManifest.model_validate_json(rows[0][0])
        if release_hash(manifest) != identity:
            raise ConfigurationError("Stored immutable release identity differs")
        return manifest

    async def active(self, channel: str) -> ReleasePointer:
        rows = await self.fresh_rows(
            "SELECT revision,active_hash,previous_hash FROM dh_m18_active WHERE channel=%s",
            (channel,),
        )
        return ReleasePointer(
            channel=channel,
            revision=rows[0][0] if rows else 0,
            active=rows[0][1] if rows else None,
            previous=rows[0][2] if rows else None,
        )

    @staticmethod
    async def check_source(cursor: Any, manifest: ReleaseManifest) -> None:
        for key, expected in sorted(manifest.review_snapshot["candidates"].items()):
            await cursor.execute(
                "SELECT body FROM dh_m18_candidates WHERE candidate_id=%s FOR SHARE", (key,)
            )
            row = await cursor.fetchone()
            if row is None or json.loads(row[0]) != expected:
                raise ConfigurationError("Reviewed source changed before publication commit")

    async def switch(
        self,
        channel: str,
        target: str,
        expected: int,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> ReleasePointer:
        manifest = await self.manifest(target)
        verify_release(manifest)
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "INSERT IGNORE INTO dh_m18_active(channel) VALUES (%s)", (channel,)
                    )
                    await cursor.execute(
                        "SELECT revision,active_hash,previous_hash FROM dh_m18_active "
                        "WHERE channel=%s FOR UPDATE",
                        (channel,),
                    )
                    row = await cursor.fetchone()
                    if row[0] != expected:
                        raise AppError(ErrorCode.VERSION_CONFLICT, "Release revision changed", 409)
                    await cursor.execute(
                        "SELECT state FROM dh_m18_releases WHERE release_hash=%s FOR SHARE",
                        (target,),
                    )
                    if (await cursor.fetchone())[0] != "READY":
                        raise ConfigurationError("Retired release cannot become active")
                    await self.check_source(cursor, manifest)
                    if row[1] == target:
                        result = ReleasePointer(
                            channel=channel, revision=row[0], active=row[1], previous=row[2]
                        )
                    else:
                        await cursor.execute(
                            "UPDATE dh_m18_active SET revision=revision+1,"
                            "previous_hash=active_hash,"
                            "active_hash=%s WHERE channel=%s AND revision=%s",
                            (target, channel, expected),
                        )
                        if cursor.rowcount != 1:
                            raise AppError(ErrorCode.VERSION_CONFLICT, "Release CAS lost", 409)
                        await cursor.execute(
                            "INSERT INTO dh_m18_release_events"
                            "(channel,revision,active_hash,previous_hash) VALUES (%s,%s,%s,%s)",
                            (channel, expected + 1, target, row[1]),
                        )
                        result = ReleasePointer(
                            channel=channel, revision=expected + 1, active=target, previous=row[1]
                        )
                    if fault:
                        fault("before_commit")
                await conn.commit()
                if fault:
                    fault("after_commit")
                return result
            except BaseException:
                await conn.rollback()
                raise

    async def retire(self, identity: str) -> None:
        """Tombstone only; source files and shared models are never implicitly deleted."""
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT active_hash,previous_hash FROM dh_m18_active "
                        "ORDER BY channel FOR UPDATE"
                    )
                    if any(identity in row for row in await cursor.fetchall()):
                        raise ConfigurationError(
                            "Active/previous complete release must be retained"
                        )
                    await cursor.execute(
                        "SELECT state FROM dh_m18_releases WHERE release_hash=%s FOR UPDATE",
                        (identity,),
                    )
                    if await cursor.fetchone() is None:
                        raise ConfigurationError("Release does not exist")
                    await cursor.execute(
                        "SELECT run_id FROM dh_m18_run_releases WHERE release_hash=%s LIMIT 1",
                        (identity,),
                    )
                    if await cursor.fetchone():
                        raise ConfigurationError(
                            "Run-referenced artifacts must be retained, "
                            "including approval/reconciliation"
                        )
                    await cursor.execute(
                        "UPDATE dh_m18_releases SET state='RETIRED' WHERE release_hash=%s",
                        (identity,),
                    )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def references(self, identity: str) -> list[dict[str, Any]]:
        rows = await self.fresh_rows(
            "SELECT x.run_id,r.status,o.status FROM dh_m18_run_releases x "
            "JOIN dh_m08_runs r ON r.run_id=x.run_id "
            "LEFT JOIN dh_m15_operations o ON o.run_id=x.run_id "
            "WHERE x.release_hash=%s ORDER BY x.run_id",
            (identity,),
        )
        return [dict(run_id=r[0], run_status=r[1], operation_status=r[2]) for r in rows]


class ReleaseRepository(ApprovalRepository):
    manifest: ReleaseManifest | None = None
    channel: str | None = None
    after_accept: Any = None

    async def current_snapshot(self, cursor: Any, op: Any, snapshot: str) -> str:
        if not self.channel:
            raise ConfigurationError("Released approval needs an authoritative channel")
        await cursor.execute(
            "SELECT active_hash FROM dh_m18_active WHERE channel=%s FOR SHARE", (self.channel,)
        )
        row = await cursor.fetchone()
        if not row or not row[0]:
            raise ConfigurationError("No complete active release for approval")
        await cursor.execute(
            "SELECT body,state FROM dh_m18_releases WHERE release_hash=%s FOR SHARE", (row[0],)
        )
        data = await cursor.fetchone()
        if not data or data[1] != "READY":
            raise ConfigurationError("Current approval release is unavailable")
        manifest = ReleaseManifest.model_validate_json(data[0])
        if release_hash(manifest) != row[0]:
            raise ConfigurationError("Current approval manifest identity differs")
        await ReleaseStore.check_source(cursor, manifest)
        return manifest.sop_snapshot

    async def prepare_question(
        self, cursor: Any, request: RequestEnvelope, fresh: Receipt
    ) -> Receipt:
        receipt = await super().prepare_question(cursor, request, fresh)
        assert self.manifest is not None
        identity = release_hash(self.manifest)
        old = receipt.question.versions.release_manifest
        if old and old != identity:
            raise ReleaseMismatch(old)
        if request.question_hint and old is None:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Legacy continuation needs its original assembly", 409
            )
        versions = receipt.question.versions.model_copy(update={"release_manifest": identity})
        question = receipt.question.model_copy(update={"versions": versions})
        await cursor.execute(
            "UPDATE dh_m08_questions SET body=%s WHERE question_id=%s",
            (question.model_dump_json(), question.question_id),
        )
        return replace(receipt, question=question)

    async def accepted(self, cursor: Any, request: RequestEnvelope, receipt: Receipt) -> None:
        assert self.manifest is not None
        identity = release_hash(self.manifest)
        await cursor.execute(
            "SELECT state FROM dh_m18_releases WHERE release_hash=%s FOR SHARE", (identity,)
        )
        row = await cursor.fetchone()
        if not row or row[0] != "READY":
            raise ConfigurationError("Selected complete release was retired before acceptance")
        await cursor.execute(
            "INSERT INTO dh_m18_run_releases(run_id,release_hash) VALUES (%s,%s)",
            (receipt.run_id, identity),
        )
        await super().accepted(cursor, request, receipt)

    async def accept(
        self, request: RequestEnvelope, *, attribution: tuple[str, int] | None = None
    ) -> Receipt:
        receipt = await super().accept(request, attribution=attribution)
        if receipt.acquired and self.after_accept:
            await self.after_accept(receipt)
        return receipt

    async def before_finish(
        self, cursor: Any, receipt: Receipt, question: Any, response: Any
    ) -> None:
        assert self.manifest is not None
        if question.versions.release_manifest != release_hash(self.manifest) or (
            response.versions.release_manifest != question.versions.release_manifest
        ):
            raise ConfigurationError("Run cannot finish with a mixed complete release")
        await super().before_finish(cursor, receipt, question, response)
