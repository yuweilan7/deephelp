"""Inspect, explicitly decide, or reconcile an original M15 operation through authenticated HTTP."""

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from deephelp_app.domain.models import ApprovalCommand, ResumeCommand
from deephelp_app.errors import ConfigurationError
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_runtime import LocalAuth
from deephelp_app.sop_acceptance import proposal_registry
from deephelp_app.sop_governance import RegistryStore


async def run(args: argparse.Namespace) -> None:
    auth = LocalAuth(local_path(args.auth))
    headers = {"Authorization": "Bearer " + auth.rows[0][0]}
    url = f"http://127.0.0.1:{args.port}/operations/{args.operation}"
    async with httpx.AsyncClient(trust_env=False, timeout=75) as client:
        if args.command == "status":
            reply = await client.get(
                url, params={"session_id": args.session, "run_id": args.run}, headers=headers
            )
        elif args.command == "resume":
            body = ResumeCommand(session_id=args.session, run_id=args.run)
            reply = await client.post(
                url + "/resume", json=body.model_dump(mode="json"), headers=headers
            )
        else:
            command = ApprovalCommand(
                session_id=args.session,
                run_id=args.run,
                decision=args.decision,
                expected_question_version=args.expected_version,
                parameters_hash=args.parameters_hash,
                sop_version=args.sop_version,
                snapshot_hash=args.snapshot_hash,
            )
            reply = await client.post(
                url + "/approval", json=command.model_dump(mode="json"), headers=headers
            )
        print(json.dumps(reply.json(), ensure_ascii=False, indent=2))
        if reply.status_code != 200:
            raise ConfigurationError(f"Approval HTTP request failed ({reply.status_code})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "status", "decide", "resume"])
    parser.add_argument("--operation")
    parser.add_argument("--run")
    parser.add_argument("--session")
    parser.add_argument("--directory", type=local_path, default=Path(".local/m15/demo-sop"))
    parser.add_argument("--key", type=local_path, default=Path(".local/m15/rights.key"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--auth", default=".local/m08/auth.json")
    parser.add_argument("--decision", choices=["approve", "reject", "revoke"])
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--parameters-hash")
    parser.add_argument("--sop-version")
    parser.add_argument("--snapshot-hash")
    args = parser.parse_args()
    if args.command == "init":
        args.key.parent.mkdir(parents=True, exist_ok=True)
        if not args.key.exists():
            with args.key.open("xb") as file:
                file.write(os.urandom(32))
        if len(args.key.read_bytes()) != 32:
            parser.error("Service key must contain exactly 32 bytes")
        RegistryStore(args.directory).publish(proposal_registry())
        print("Synthetic approval registry and service key ready; existing key retained")
        return
    if not all((args.operation, args.run, args.session)):
        parser.error("status/decide/resume require operation, original run and session")
    if args.command == "decide" and not all(
        (
            args.decision,
            args.expected_version,
            args.parameters_hash,
            args.sop_version,
            args.snapshot_hash,
        )
    ):
        parser.error("decide requires the displayed decision, version, parameter and SOP bindings")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
