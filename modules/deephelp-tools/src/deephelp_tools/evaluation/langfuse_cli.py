"""Explicit historical import; no evaluation runner or model gateway is dispatched."""

import argparse
import asyncio
from pathlib import Path

import httpx

from deephelp_app.adapters.native_observation import configuration
from deephelp_tools.evaluation.langfuse_mapping import Platform, plan_import, upload


async def run(args: argparse.Namespace) -> None:
    plan = plan_import(args.report, args.data, args.manifest, args.case, args.mode)
    async with httpx.AsyncClient() as client:
        state = await upload(plan, args.state, Platform(client, configuration(str(args.config))))
    print(f"{state['status']}: {state['expected_rows']} historical rows; model calls 0")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", required=True, action="store_true")
    for name in ("report", "data", "manifest", "state", "config"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--case", required=True, action="append")
    parser.add_argument("--mode", required=True, action="append")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
