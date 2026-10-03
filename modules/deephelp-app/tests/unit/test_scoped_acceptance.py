"""Acceptance selection, remote isolation and usage reporting, without real models."""

import argparse
import importlib
import json
import socket
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.core import MODES, audit, targeted_gate
from deephelp_app.evaluation.evaluation_cli import select_scope
from deephelp_app.gateway import QianwenGateway, diagnostic_usage
from deephelp_app.probes.live_probe import probe, selected_capabilities
from deephelp_app.probes.text_entity_probe import selected_cases
from deephelp_app.probes.usage import api_records, summarize_usage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("host", ["example.invalid", "192.0.2.1", "2001:db8::1"])
def test_pytest_blocks_remote_dns_and_connect_before_any_model_request(host):
    with pytest.raises(RuntimeError, match="Offline pytest"):
        socket.getaddrinfo(host, 443)
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="Offline pytest"):
            connection.connect((host, 443))
        with pytest.raises(RuntimeError, match="Offline pytest"):
            connection.connect_ex((host, 443))


def test_loopback_protocol_tests_remain_available():
    assert socket.getaddrinfo("localhost", 80)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.socket() as client:
            client.connect(server.getsockname())
            peer, _ = server.accept()
            peer.close()


@pytest.mark.parametrize(
    "module",
    [
        "deephelp_app.mvp_cli",
        "deephelp_app.dense_cli",
        "deephelp_app.evaluation.hybrid_cli",
        "deephelp_app.probes.live_probe",
        "deephelp_app.probes.text_entity_probe",
        "deephelp_app.evaluation.evaluation_cli",
        "deephelp_app.probes.release_probe",
        "deephelp_app.flywheel_cli",
    ],
)
def test_import_and_help_do_not_request_models(module, monkeypatch):
    def forbid_request(*args, **kwargs):
        pytest.fail("Import/help must not request a model")

    monkeypatch.setattr(QianwenGateway, "chat", forbid_request)
    monkeypatch.setattr(QianwenGateway, "embed", forbid_request)
    imported = importlib.import_module(module)
    monkeypatch.setattr(sys, "argv", [module, "--help"])
    with pytest.raises(SystemExit) as exited:
        imported.main()
    assert exited.value.code == 0


def scope(**kwargs):
    return argparse.Namespace(**dict(live=kwargs.pop("live", True), all_cases=False, **kwargs))


def test_live_requires_explicit_scope_and_full_evaluation_is_separate():
    cases, _ = audit()
    with pytest.raises(ConfigurationError, match="Targeted live"):
        select_scope(scope(), cases)
    chosen, modes = select_scope(scope(case=[cases[0].case_id], mode=["memory_event"]), cases)
    assert chosen == [cases[0]] and modes == ["memory_event"]
    assert select_scope(scope(full_evaluation=True), cases)[1] == list(MODES)
    assert select_scope(scope(live=False), cases) == (cases, list(MODES))


@pytest.mark.parametrize("change", ["unknown", "duplicate", "split", "all", "approvals"])
def test_invalid_selection_fails_before_live_assembly(change):
    cases, _ = audit()
    args = scope(case=[cases[0].case_id], mode=["memory_event"])
    if change == "unknown":
        args.case = ["unknown"]
    elif change == "duplicate":
        args.mode *= 2
    elif change == "split":
        args.split = "test" if cases[0].split != "test" else "regression"
    elif change == "all":
        args.all_cases = True
    else:
        args.approvals = True
    with pytest.raises(ConfigurationError):
        select_scope(args, cases)


def test_multiturn_selection_keeps_prerequisites_and_targeted_gate_keeps_safety():
    cases, _ = audit()
    case = next(c for c in cases if len(c.turns) > 1)
    selected, _ = select_scope(scope(case=[case.case_id], mode=["memory_event"]), cases)
    assert selected[0].turns == case.turns
    route = {
        "rows": [{"score": {"completed": True, "hard_failures": []}}],
        "checks": {"persisted": True, "zero_call_replay": True},
        "metrics": {"event_wrong_merge": {"numerator": 0}},
    }
    assert targeted_gate({"memory_event": route}, complete=True)["accepted"]
    assert not targeted_gate({"memory_event": route}, complete=False)["accepted"]
    route["rows"][0]["score"]["hard_failures"] = ["unsupported_answer"]
    assert not targeted_gate({"memory_event": route}, complete=True)["accepted"]


def test_entity_scope_does_not_silently_expand_to_full_corpus():
    chosen = selected_cases(argparse.Namespace(case=["quoted-order"]))
    assert [c["id"] for c in chosen] == ["quoted-order"]
    with pytest.raises(ConfigurationError):
        selected_cases(argparse.Namespace())
    with pytest.raises(ConfigurationError):
        selected_cases(argparse.Namespace(case=["unknown"]))


@pytest.mark.parametrize(
    "capability", ["chat", "schema", "tool", "embed", "extended_chat", "thinking_schema"]
)
async def test_selected_capability_makes_one_mock_api_call_and_persists_usage(
    capability, tmp_path, monkeypatch
):
    import deephelp_app.probes.live_probe as cli

    state = tmp_path / "budget.json"
    state.write_text(
        json.dumps(
            dict(
                max_calls=8,
                max_tokens=200000,
                max_cost_cny="10",
                attempts=0,
                tokens=0,
                charged_tokens=0,
                cost_upper_cny="0",
                uncertain_attempts=0,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "local_path", lambda path: Path(path))
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        usage = {
            "prompt_tokens": 30,
            "completion_tokens": 4,
            "total_tokens": 34,
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
        if "input" in body:
            usage = {"prompt_tokens": 30, "total_tokens": 30}
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "usage": usage,
                    "data": [
                        {"index": i, "embedding": [1.0] + [0.0] * 1023}
                        for i, _ in enumerate(body["input"])
                    ],
                },
            )
        message = {"role": "assistant", "content": "OK"}
        finish = "stop"
        if "response_format" in body:
            message["content"] = '{"order_id":"0007","status":"synthetic"}'
        if body.get("enable_thinking"):
            message["reasoning_content"] = "synthetic reasoning"
        if "tools" in body:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "synthetic_lookup",
                            "arguments": '{"order_id":"0007"}',
                        },
                    }
                ],
            }
            finish = "tool_calls"
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": usage,
                "choices": [{"message": message, "finish_reason": finish}],
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        cli.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), trust_env=False),
    )
    monkeypatch.setenv("DEEPHELP_MODEL_BASE_URL", "https://synthetic.invalid")
    monkeypatch.setenv("DEEPHELP_MODEL_API_KEY", "SYNTHETIC-KEY")
    output = tmp_path / "first.json"
    args = argparse.Namespace(
        capability=[capability],
        all_capabilities=False,
        extended=False,
        stage="feature",
        budget_state=str(state),
        output=str(output),
        max_calls=1,
        max_tokens=200000,
        max_cost=Decimal("1"),
        providers="modules/deephelp-app/providers.example.json",
    )
    assert await probe(args) == 0
    assert len(requests) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["selected_capabilities"] == [capability]
    assert report["api_usage"]["api_attempts"] == 1
    assert report["api_usage"]["by_phase_model_sample"][0]["sample"] == capability
    assert report["api_usage"]["total_tokens"] == (30 if capability == "embed" else 34)
    assert report["api_usage"]["reasoning_tokens"] == (None if capability == "embed" else 2)
    # Same run keeps its cumulative budget; changing the report path does not reset it.
    args.output = str(tmp_path / "second.json")
    assert await probe(args) == 0
    assert json.loads(state.read_text(encoding="utf-8"))["attempts"] == 2
    with pytest.raises(ConfigurationError, match="immutable"):
        await probe(args)


def test_capabilities_require_selection_without_stage_based_expansion():
    with pytest.raises(ConfigurationError):
        selected_capabilities(argparse.Namespace(extended=False))
    assert selected_capabilities(
        argparse.Namespace(
            capability=["schema"],
            stage="main",
            extended=False,
        )
    ) == ["schema"]
    assert len(selected_capabilities(argparse.Namespace(all_capabilities=True, extended=True))) == 6


def test_usage_unknowns_reservations_and_diagnostic_rollover():
    before = [{"path": "chat", "api_usage": diagnostic_usage({"total_tokens": 999})}]
    known = {
        "path": "chat",
        "model": "a",
        "api_usage": diagnostic_usage(
            {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        ),
    }
    records = api_records([known], previous=before, phase="schema", sample="s1")
    result = summarize_usage(records, attempts=1)
    assert result["total_tokens"] == 28  # reasoning is not added again
    assert result["by_phase_model_sample"][0]["sample"] == "s1"
    result = summarize_usage(records, attempts=2)
    assert result["total_tokens"] is None and result["observed_total_tokens"] == 28
    assert result["unobserved_attempts"] == 1
    assert diagnostic_usage({"total_tokens": True})["total_tokens"] is None
    assert summarize_usage([], attempts=0)["total_tokens"] is None
