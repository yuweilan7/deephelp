"""合成权益进程/响应故障练习，原效果与丢响应分别注入。

before_effect/after_effect说明提交前后边界，必须按原operation查询持久效果。
工作进程/entered标记供拥有者续跑与诊断，不在成功前随意清理。
"""

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
    if fault == "hold_before_effect":
        from deephelp_app.application.dense import atomic_json

        await asyncio.to_thread(
            atomic_json, path.with_suffix(".entered.json"), {"operation": operation_id}
        )
        # Explicit, bounded learning fault only. The existing effect row lock
        # stays held so the acceptance controller can observe blocking query.
        async with asyncio.timeout(5):
            for _ in range(250):
                if (
                    json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8")).get(
                        operation_id
                    )
                    != fault
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                raise TimeoutError("Owned effect hold was not released")
    return fault


def after_effect(fault: str | None) -> None:
    if fault == "crash_after_effect":
        os._exit(86)
    if fault == "lost_response":
        raise HTTPException(503, "Injected response loss after effect")
