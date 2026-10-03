"""Explicit synthetic live acceptance with persistent, locked, cumulative budgets."""

import argparse
import asyncio
import hashlib
import json
import platform
import sys
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import httpx

from deephelp_app.domain.models import ChatMessage, ChatRequest, ErrorCode, ModelTool
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import validate_control_paths
from deephelp_app.probes.usage import api_records, summarize_usage
from deephelp_app.providers import ProviderConfig, create_gateway

PROBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "status": {"type": "string", "enum": ["synthetic"]},
    },
    "required": ["order_id", "status"],
    "additionalProperties": False,
}


CAPABILITIES = ("chat", "schema", "tool", "embed", "extended_chat", "thinking_schema")


def selected_capabilities(args: argparse.Namespace) -> list[str]:
    selected = list(getattr(args, "capability", None) or [])
    if getattr(args, "all_capabilities", False):
        if selected:
            raise ConfigurationError("Choose --capability or --all-capabilities, not both")
        selected = list(CAPABILITIES[:4])
        if args.extended:
            selected += list(CAPABILITIES[4:])
    elif args.extended:
        raise ConfigurationError(
            "--extended requires --all-capabilities; individual checks use --capability"
        )
    if (
        not selected
        or len(selected) != len(set(selected))
        or any(c not in CAPABILITIES for c in selected)
    ):
        raise ConfigurationError("Select unique --capability values or explicit --all-capabilities")
    return selected


async def probe(args: argparse.Namespace) -> int:
    capabilities = selected_capabilities(args)
    state_path = local_path(args.budget_state)
    output = local_path(args.output)
    validate_control_paths([state_path, Path(args.providers)], output=output)
    if output.exists():
        raise ConfigurationError("Use a new immutable capability report path")
    if not state_path.exists():
        raise ConfigurationError("Initialize an explicitly authorized cumulative budget first")
    lock_path = state_path.with_suffix(".lock")
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ConfigurationError("Live budget already locked; inspect before recovery") from None
    budget: ExecutionBudget | None = None
    report: dict[str, object] = {
        "stage": args.stage,
        "status": "PENDING_LIVE",
        "results": [],
        "selected_capabilities": capabilities,
        "environment": {
            "python": platform.python_version(),
            "httpx": version("httpx"),
            "jsonschema": version("jsonschema"),
        },
    }
    output = local_path(args.output)
    try:
        original = json.loads(state_path.read_text(encoding="utf-8-sig"))
        required_calls = len(capabilities)
        remaining_calls = original["max_calls"] - original["attempts"]
        remaining_tokens = original["max_tokens"] - original["charged_tokens"]
        initial_cost = Decimal(
            str(original.get("cost_upper_cny", original["charged_tokens"] * 0.000008))
        )
        remaining_cost = Decimal(original["max_cost_cny"]) - initial_cost
        if (
            args.max_calls < required_calls
            or remaining_calls < required_calls
            or args.max_tokens <= 0
            or not args.max_cost.is_finite()
            or args.max_cost <= 0
        ):
            raise ConfigurationError("Insufficient explicit live budgets")
        budget = ExecutionBudget.start(
            120,
            min(args.max_calls, remaining_calls),
            0,
            token_limit=min(args.max_tokens, remaining_tokens),
            cost_limit=min(args.max_cost, remaining_cost),
        )

        def persist() -> None:
            assert budget is not None
            state = dict(original)
            state.update(
                attempts=original["attempts"] + budget.attempts_used,
                tokens=original["tokens"] + budget.tokens_used,
                charged_tokens=original["charged_tokens"] + budget.token_upper_bound,
                cost_upper_cny=str(initial_cost + budget.cost_upper_bound),
                active_stage=args.stage,
                uncertain_attempts=original.get("uncertain_attempts", 0)
                + budget.uncertain_attempts,
            )
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        budget.on_change = persist
        config = ProviderConfig.load(Path(args.providers))
        report["provider_config_sha256"] = hashlib.sha256(
            config.model_dump_json().encode()
        ).hexdigest()
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            gateway = create_gateway(client, config, AsyncCalls(1, 30))
            results: list[dict[str, object]] = []
            observations = []
            previous: list[dict[str, object]] = []
            active: str | None = None

            def observe(capability: str | None) -> None:
                nonlocal previous, active
                if active:
                    observations.extend(
                        api_records(
                            gateway.diagnostics, phase=args.stage, sample=active, previous=previous
                        )
                    )
                previous, active = list(gateway.diagnostics), capability

            try:
                if "chat" in capabilities:
                    observe("chat")
                    chat = await gateway.chat(
                        ChatRequest(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content=(
                                        "Synthetic gateway probe. Reply exactly OK. "
                                        "No other output."
                                    ),
                                )
                            ]
                        ),
                        budget,
                    )
                    report["chat_probe"] = {
                        "content": chat.content,
                        "provider_request_id": chat.provider_request_id,
                    }
                    if (chat.content or "").strip() != "OK":
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic chat expectation failed"
                        )
                    results.append(
                        {
                            "capability": "chat",
                            "model": chat.model,
                            "usage": chat.usage.model_dump(),
                            "finish_reason": chat.finish_reason,
                            "provider_request_id": chat.provider_request_id,
                        }
                    )
                if "schema" in capabilities:
                    observe("schema")
                    schema = await gateway.chat(
                        ChatRequest(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content="Return JSON with order_id 0007 and status synthetic.",
                                )
                            ],
                            response_format="json_schema",
                            output_schema=PROBE_SCHEMA,
                        ),
                        budget,
                    )
                    if schema.structured != {"order_id": "0007", "status": "synthetic"}:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic schema expectation failed"
                        )
                    results.append(
                        {
                            "capability": "schema",
                            "usage": schema.usage.model_dump(),
                            "provider_request_id": schema.provider_request_id,
                        }
                    )
                if "tool" in capabilities:
                    observe("tool")
                    tool = await gateway.chat(
                        ChatRequest(
                            messages=[
                                ChatMessage(
                                    role="user", content="Call synthetic_lookup with order_id 0007."
                                )
                            ],
                            tools=[
                                ModelTool(
                                    name="synthetic_lookup",
                                    description="Read a synthetic order.",
                                    parameters={
                                        "type": "object",
                                        "properties": {"order_id": {"type": "string"}},
                                        "required": ["order_id"],
                                        "additionalProperties": False,
                                    },
                                )
                            ],
                            tool_choice="synthetic_lookup",
                        ),
                        budget,
                    )
                    if tool.tool_calls[0].arguments != {"order_id": "0007"}:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic tool expectation failed"
                        )
                    # Correlate a controlled local result with the call ID; no business I/O.
                    controlled_result = ChatMessage(
                        role="tool",
                        tool_call_id=tool.tool_calls[0].call_id,
                        content='{"status":"synthetic"}',
                    )
                    results.append(
                        {
                            "capability": "tool",
                            "usage": tool.usage.model_dump(),
                            "provider_request_id": tool.provider_request_id,
                            "finish_reason": tool.finish_reason,
                            "controlled_result_correlated": bool(controlled_result.tool_call_id),
                        }
                    )
                if "embed" in capabilities:
                    observe("embed")
                    embedding = await gateway.embed(
                        ["synthetic order 0007", "synthetic coupon 0008", "synthetic order 0007"],
                        budget,
                    )
                    if embedding.vectors[0] != embedding.vectors[2]:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID,
                            "Synthetic duplicate input mapping failed",
                        )
                    before = budget.usage()
                    cached = await gateway.embed(
                        ["synthetic coupon 0008", "synthetic order 0007"], budget
                    )
                    if budget.usage() != before or cached.vectors != [
                        embedding.vectors[1],
                        embedding.vectors[0],
                    ]:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Embedding cache acceptance failed"
                        )
                    results.append(
                        {
                            "capability": "embedding",
                            "usage": embedding.usage.model_dump(),
                            "signature": embedding.signature.model_dump(),
                            "fingerprint": embedding.signature.fingerprint,
                            "rows": len(embedding.vectors),
                            "dimension": len(embedding.vectors[0]),
                            "position_index_fallback": embedding.position_index_fallback,
                            "provider_request_ids": embedding.provider_request_ids,
                            "cache_hits": cached.cache_hits,
                            "cache_extra_calls": 0,
                        }
                    )
                if "extended_chat" in capabilities:
                    observe("extended_chat")
                    long_messages = [
                        ChatMessage(role="user", content="Synthetic context. " * 100)
                        for _ in range(40)
                    ]
                    long_messages.append(
                        ChatMessage(role="user", content="Reply exactly OK. No other output.")
                    )
                    extended = await gateway.chat(
                        ChatRequest(messages=long_messages, max_output_tokens=8192), budget
                    )
                    if (extended.content or "").strip() != "OK":
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Long context content differs"
                        )
                    results.append(
                        {
                            "capability": "extended_chat",
                            "message_count": len(long_messages),
                            "content_bytes": sum(
                                len(m.content.encode()) for m in long_messages if m.content
                            ),
                            "requested_output_tokens": 8192,
                            "usage": extended.usage.model_dump(),
                            "provider_request_id": extended.provider_request_id,
                        }
                    )
                if "thinking_schema" in capabilities:
                    observe("thinking_schema")
                    thinking = await gateway.chat(
                        ChatRequest(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content=(
                                        "Compute 17*19 internally, then return JSON with "
                                        "order_id 0007 and status synthetic."
                                    ),
                                )
                            ],
                            response_format="json_schema",
                            output_schema=PROBE_SCHEMA,
                            max_output_tokens=2048,
                            enable_thinking=True,
                            thinking_budget=2048,
                        ),
                        budget,
                    )
                    diagnostic = gateway.diagnostics[-1]
                    if (
                        thinking.structured != {"order_id": "0007", "status": "synthetic"}
                        or not diagnostic.get("thinking_enabled")
                        or not diagnostic.get("reasoning_content_length")
                    ):
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Thinking/schema content differs"
                        )
                    results.append(
                        {
                            "capability": "thinking_schema",
                            "thinking_enabled": True,
                            "reasoning_content_length": diagnostic["reasoning_content_length"],
                            "usage": thinking.usage.model_dump(),
                            "provider_request_id": thinking.provider_request_id,
                        }
                    )
                report.update(status="PASS")
            except AppError as exc:
                report.update(
                    status="FAIL",
                    error={
                        "code": exc.code,
                        "provider_code": exc.provider_code,
                        "provider_request_id": exc.provider_request_id,
                    },
                )
            finally:
                observe(None)
                report.update(
                    results=results,
                    budget_used=budget.usage().model_dump(mode="json"),
                    diagnostics=gateway.diagnostics,
                    api_usage=summarize_usage(
                        observations,
                        attempts=budget.attempts_used,
                    ),
                )
                persist()
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current.setdefault("stages", []).append(report)
                current.pop("active_stage", None)
                state_path.write_text(
                    json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["status"] == "PASS" else 1
    finally:
        lock.close()
        lock_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], required=True)
    parser.add_argument("--capability", action="append", choices=CAPABILITIES)
    parser.add_argument(
        "--all-capabilities", action="store_true", help="Explicit full capability check"
    )
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Also verify larger context/output and thinking/schema",
    )
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--max-cost", type=Decimal, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Real model requests require --live and explicit budgets")
    try:
        return asyncio.run(probe(args))
    except (ConfigurationError, ValueError, OSError) as exc:
        # Configuration errors are static; arbitrary parsing/OS exceptions may contain data.
        print(
            str(exc) if isinstance(exc, ConfigurationError) else "Live local setup failed",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
