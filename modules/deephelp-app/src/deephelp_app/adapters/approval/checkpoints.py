"""Fact migrations and a configuration key helper; persistence is the official saver."""

from importlib.resources import files
from typing import Any

from langchain_core.runnables import RunnableConfig


async def migrate_m15(pool: Any) -> None:
    sql = (
        files("deephelp_app")
        .joinpath("resources/migrations/015_approval.sql")
        .read_text(encoding="utf-8")
    )
    sql += "\n;" + files("deephelp_app").joinpath(
        "resources/migrations/021_observation.sql"
    ).read_text(encoding="utf-8")
    sql += "\n;" + files("deephelp_app").joinpath(
        "resources/migrations/028_observation.sql"
    ).read_text(encoding="utf-8")
    async with pool.acquire() as conn, conn.cursor() as cursor:
        for statement in sql.split(";"):
            if statement.strip():
                await cursor.execute(statement)
        await conn.commit()


def configuration(thread: str, ns: str, checkpoint: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": checkpoint}}
