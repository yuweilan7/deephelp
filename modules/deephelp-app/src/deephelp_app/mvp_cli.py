"""Loopback entry point. Explicit commands initialize, migrate, serve, or query the MVP."""

import argparse
import asyncio
import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

from deephelp_app.approval_store import ApprovalRepository
from deephelp_app.domain.models import ConverseInput
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import (
    BudgetSession,
    LiveAssembly,
    LocalAuth,
    live_app,
    validate_control_paths,
    validate_trace_path,
)


def initialize(args: argparse.Namespace) -> None:
    auth, budget = local_path(args.auth), local_path(args.budget_state)
    validate_control_paths([auth, budget])
    cost = Decimal(args.max_cost)
    if args.max_calls < 1 or args.max_tokens < 1 or not cost.is_finite() or cost <= 0:
        raise ConfigurationError("Initialize positive call/token/cost limits")
    if auth == budget or auth.exists() or budget.exists():
        raise ConfigurationError("Use distinct new local auth and budget files; never overwrite")
    auth.write_text(
        json.dumps(
            {
                "tokens": [
                    {
                        "token": secrets.token_urlsafe(32),
                        "identity": {
                            "tenant_id": "synthetic-tenant",
                            "user_id": "synthetic-user-a",
                        },
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    budget.write_text(
        json.dumps(
            {
                "max_calls": args.max_calls,
                "max_tokens": args.max_tokens,
                "max_cost_cny": str(args.max_cost),
                "attempts": 0,
                "tokens": 0,
                "charged_tokens": 0,
                "cost_upper_cny": "0",
                "uncertain_attempts": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Initialized local auth and cumulative budget; token is only in the auth file")


async def migrate() -> None:
    ledger = await ApprovalRepository.open(Path.cwd())
    try:
        async with asyncio.timeout(30):
            await ledger.migrate()
    finally:
        await ledger.aclose()
    print("M08/M10/M15 MySQL project tables ready")


async def ask(args: argparse.Namespace) -> None:
    auth = LocalAuth(local_path(args.auth))
    message = ConverseInput(
        channel="cli",
        session_id=args.session,
        message_id=args.message_id,
        raw_text=args.text,
        occurred_at=args.occurred_at,
        question_hint=args.question_hint,
        expected_question_version=args.question_version,
    )
    async with httpx.AsyncClient(trust_env=False, timeout=100) as client:
        reply = await client.post(
            f"http://127.0.0.1:{args.port}/converse",
            headers={"Authorization": "Bearer " + auth.rows[0][0]},
            json=message.model_dump(mode="json"),
        )
    print(json.dumps(reply.json(), ensure_ascii=False, indent=2))
    if reply.status_code != 200:
        raise ConfigurationError("MVP HTTP request failed")


def main() -> int:
    p = argparse.ArgumentParser(description="DeepHelp authenticated single-message MVP")
    p.add_argument("command", choices=["init", "migrate", "serve", "ask"])
    p.add_argument("--question-hint")
    p.add_argument("--question-version", type=int)
    p.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    p.add_argument("--pointer", default=".local/m05/active.json")
    p.add_argument("--fasttext-pointer", help="Explicitly enable a dev-calibrated M13 artifact")
    p.add_argument(
        "--reply-polish", action="store_true", help="Optional closed-vocabulary M16 wording"
    )
    p.add_argument("--trace-path", default=".local/m08/trace.jsonl")
    p.add_argument("--rights-port", type=int, help="Explicit M15 synthetic rights loopback service")
    p.add_argument("--rights-key", help="32-byte local service signing key")
    p.add_argument(
        "--sop-directory", help="Published M14 registries; omitted uses bundled registry"
    )
    p.add_argument("--auth", default=".local/m08/auth.json")
    p.add_argument("--budget-state", default=".local/m08/session-budget.json")
    p.add_argument("--max-calls", type=int, default=300)
    p.add_argument("--max-tokens", type=int, default=600000)
    p.add_argument("--max-cost", type=str, default="10")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--session", default="cli-session")
    p.add_argument("--message-id", default=uuid4().hex)
    p.add_argument("--occurred-at", default=datetime.now(UTC).isoformat())
    p.add_argument("--text", default="订单 000031 未享受优惠，请查一下")
    args = p.parse_args()
    try:
        if args.command == "init":
            initialize(args)
        elif args.command == "migrate":
            asyncio.run(migrate())
        elif args.command == "ask":
            asyncio.run(ask(args))
        else:
            validate_trace_path(
                local_path(args.trace_path),
                [
                    local_path(args.auth),
                    local_path(args.budget_state),
                    local_path(args.pointer),
                    Path(args.providers),
                ],
            )
            gate = BudgetSession(local_path(args.budget_state))
            gate.open()
            try:
                assembly = LiveAssembly(
                    Path.cwd(),
                    Path(args.providers),
                    local_path(args.pointer),
                    fasttext_pointer=local_path(args.fasttext_pointer)
                    if args.fasttext_pointer
                    else None,
                    sop_directory=local_path(args.sop_directory) if args.sop_directory else None,
                    reply_polish=args.reply_polish,
                    rights_port=args.rights_port,
                    rights_key=local_path(args.rights_key) if args.rights_key else None,
                )
                app = live_app(
                    assembly,
                    LocalAuth(local_path(args.auth)),
                    gate,
                    trace_path=str(local_path(args.trace_path)),
                )
                uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
            finally:
                gate.close()
    except (AppError, ConfigurationError) as exc:
        print(json.dumps({"status": "FAIL", "message": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
