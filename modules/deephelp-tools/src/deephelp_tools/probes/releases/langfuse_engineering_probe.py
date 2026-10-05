"""Fixed historical import/readback/recovery proof; model and business calls are zero."""

# 固定历史上传、读回与失败/取消续传验证，模型和业务请求为零。
#
# 原报告的时间/usage保持历史含义；导入正确并不等于重新运行模型内容。
# 子进程或SDK队列由本次调用关闭，必要部分state保留供同来源恢复。

import argparse
import asyncio
import time
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.adapters.observability.native_observation import configuration
from deephelp_app.application.dense import atomic_json, load_json
from deephelp_tools.evaluation.platform.langfuse_mapping import FROM, Platform, plan_import, upload


async def import_proof(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live or args.output.exists():
        raise ValueError("Explicit live and a new evidence path are required")
    if any((args.state_root / name).exists() for name in ("disconnect.json", "cancelled.json")):
        raise ValueError("Recovery proof requires a fresh state directory")
    plan = plan_import(args.report, args.data, args.manifest, args.case, args.mode)
    config = configuration(str(args.config))
    args.state_root.mkdir(parents=True, exist_ok=True)
    ready = asyncio.Event()

    class Boundary(Platform):
        successes = 0
        cancel = False

        async def verify(self, row: dict[str, Any], source: dict[str, Any]) -> bool:
            if self.successes:
                if self.cancel:
                    ready.set()
                    await asyncio.Event().wait()
                # An actual refused loopback connection at the next read boundary.
                await self.client.get("http://127.0.0.1:1", timeout=1)
            found = await super().verify(row, source)
            self.successes += int(found)
            return found

    checks: dict[str, bool] = {}
    async with httpx.AsyncClient() as client, asyncio.timeout(600):
        platform = Platform(client, config)
        failure_state = args.state_root / "disconnect.json"
        try:
            await upload(plan, failure_state, Boundary(client, config))
        except httpx.TransportError:
            state: dict[str, Any] = load_json(failure_state)
            checks["actual_disconnect_preserves_partial"] = (
                state["status"] == "PARTIAL" and len(state["rows"]) == 1
            )
        recovered = await upload(plan, failure_state, platform)
        checks["disconnect_recovery_complete"] = recovered["status"] == "COMPLETE"
        boundary = Boundary(client, config)
        boundary.cancel = True
        cancelled_state = args.state_root / "cancelled.json"
        task = asyncio.create_task(upload(plan, cancelled_state, boundary))
        await ready.wait()
        started = time.monotonic()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            cancelled: dict[str, Any] = load_json(cancelled_state)
            checks["cancel_preserves_partial"] = (
                cancelled["status"] == "CANCELLED" and len(cancelled["rows"]) == 1
            )
        cancellation_ms = (time.monotonic() - started) * 1000
        final = await upload(plan, cancelled_state, platform)
        checks["cancel_recovery_complete"] = final["status"] == "COMPLETE"
        repeated = await upload(plan, cancelled_state, platform)
        checks["retransmit_complete"] = repeated["rows"] == final["rows"]
        items = await platform.request(
            "GET", "dataset-items", params={"datasetName": plan["dataset_name"], "limit": 100}
        )
        experiments = await platform.request(
            "GET",
            "experiment-items",
            params={"fromStartTime": FROM, "experimentId": plan["experiment_id"], "limit": 100},
        )
        checks["exact_dataset_coverage"] = {r["id"] for r in items["data"]} == {
            r["item_id"] for r in plan["rows"]
        }
        checks["exact_experiment_coverage"] = len(experiments["data"]) == len(plan["rows"]) and {
            r["id"] for r in experiments["data"]
        } == {r["observation_id"] for r in plan["rows"]}
    result = dict(
        status="PASS" if all(checks.values()) else "FAIL",
        checks=checks,
        cancellation_ms=cancellation_ms,
        rows=len(plan["rows"]),
        model_calls=0,
        source_report_sha256=plan["summary"]["source_report_sha256"],
        dataset_url=final["dataset_url"],
        experiment_url=final["experiment_url"],
        note="Historical import and live platform protocol only",
    )
    atomic_json(args.output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    for name in ("config", "report", "data", "manifest", "state-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--case", required=True, action="append")
    parser.add_argument("--mode", required=True, action="append")
    result = asyncio.run(import_proof(parser.parse_args()))
    print(f"{result['status']}: {result['rows']} historical rows; model calls 0")
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
