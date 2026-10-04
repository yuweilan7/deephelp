"""M11 stateless preview or explicit live persistent event aggregation."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from deephelp_app.adapters.providers import EndpointConfig, ProviderConfig
from deephelp_app.application.event_cluster import EventAggregationService
from deephelp_app.bootstrap.event_runtime import live_events
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, validate_control_paths
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_tools.learning.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.event_replay import ReplayJudge, envelope


async def run(args: argparse.Namespace) -> None:
    request = envelope(
        args.text,
        args.session,
        channel=args.channel,
        message_id=args.message_id or uuid4().hex,
        question_hint=args.question_hint,
        expected_question_version=args.expected_version,
    )
    if args.occurred_at:
        request = request.model_copy(
            update={"occurred_at": datetime.fromisoformat(args.occurred_at)}
        )
        request = type(request).model_validate(request.model_dump())
    if args.command == "preview":
        result = await EventAggregationService(MemoryCaseRepository(), ReplayJudge()).aggregate(
            request,
            ExecutionBudget.start(30, 5, 0),
            stateless_preview=True,
        )
        print(result.model_dump_json(indent=2))
        return
    if not args.live or not args.budget_state:
        raise ConfigurationError("Persistent aggregation requires --live and --budget-state")
    gate = BudgetSession(local_path(args.budget_state))
    validate_control_paths([gate.path, Path(args.providers), Path(args.judge_provider)])
    gate.open()
    try:
        async with live_events(
            Path.cwd(),
            ProviderConfig.load(Path(args.providers)),
            collection=args.collection,
            judge_endpoint=EndpointConfig.model_validate_json(
                await asyncio.to_thread(Path(args.judge_provider).read_text, encoding="utf-8")
            ),
        ) as (events, _, _, _):
            response = await events.process(request, gate.request())
            print(response.model_dump_json(indent=2))
    finally:
        gate.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preview", "aggregate"])
    parser.add_argument("--text", required=True)
    parser.add_argument("--session", default="m11-demo")
    parser.add_argument("--channel", default="m11-demo")
    parser.add_argument("--message-id")
    parser.add_argument(
        "--occurred-at", help="Reuse the original aware timestamp for message replay"
    )
    parser.add_argument("--question-hint")
    parser.add_argument("--expected-version", type=int)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--budget-state")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--judge-provider", default="modules/deephelp-app/event-judge.example.json")
    parser.add_argument("--collection")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
        return 0
    except (AppError, ConfigurationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
