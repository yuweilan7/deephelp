"""Read current feedback facts for immutable runtime releases."""

from pathlib import Path
from typing import Any

from deephelp_app.adapters.ledger import MySQLLedger
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.feedback import FeedbackCandidate


class FeedbackReader(MySQLLedger):
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
        source = Path(__file__).parents[1] / "resources/migrations/018_flywheel.sql"
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

    async def get(self, candidate_id: str) -> FeedbackCandidate:
        rows = await self.fresh_rows(
            "SELECT body FROM dh_m18_candidates WHERE candidate_id=%s", (candidate_id,)
        )
        if not rows:
            raise ConfigurationError("Feedback candidate not found")
        return FeedbackCandidate.model_validate_json(rows[0][0])

    async def require_current(self, source: dict[str, Any]) -> None:
        for key, raw in source["candidates"].items():
            current = await self.get(key)
            if current.model_dump(mode="json") != raw:
                raise ConfigurationError(
                    "Reviewed source changed; rebuild, never publish stale derivatives"
                )
