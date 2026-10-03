"""Owned synthetic rights process/response fault exercises, opt-in only."""

import asyncio
import json
import os
from pathlib import Path

from fastapi import HTTPException


async def before_effect(path: Path, operation_id: str) -> str | None:
    fault: str | None = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8")).get(
        operation_id
    )
    if fault == "before_effect":
        raise HTTPException(503, "Injected before effect")
    return fault


def after_effect(fault: str | None) -> None:
    if fault == "crash_after_effect":
        os._exit(86)
    if fault == "lost_response":
        raise HTTPException(503, "Injected response loss after effect")
