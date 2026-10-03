import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from deephelp_app.debug import export_json, sanitize, scope_hash
from deephelp_app.domain.models import (
    ChatResult,
    DebugSnapshot,
    EvidenceRef,
    Fact,
    ModelUsage,
    Money,
    SOPResult,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.mvp_runtime import validate_trace_path
from deephelp_app.reply import ReplyComposer, apply_wording, fact_reply
from deephelp_app.trace import JsonlTrace, TraceEvent, observe_sop_node, sop_node_observer

pytestmark = pytest.mark.unit


def result():
    return SOPResult(
        status="RESOLVED",
        sop_id="test",
        sop_version="v1",
        next_action="none",
        facts=(
            Fact(name="order_id", kind="text", value="000031", evidence_ids=("e",)),
            Fact(
                name="paid",
                kind="money",
                value=Money(amount="99.90", currency="CNY"),
                evidence_ids=("e",),
            ),
        ),
        evidence_refs=(EvidenceRef(evidence_id="e", source="tool", record_id="call"),),
        tool_call_ids=("call",),
    )


@pytest.mark.parametrize(
    "proposal",
    [
        {"opening": "退款100元已到账", "closing": "none"},
        {"opening": "checked", "closing": "none", "amount": "100.00"},
        {"opening": "checked", "closing": "none", "order_id": "000042"},
        {"opening": "checked", "closing": "none", "executed": True},
        {"opening": "checked", "closing": "none", "evidence": "invented"},
        {"opening": "checked", "closing": "none", "reply": "退款已到账"},
        {"opening": "checked"},
        None,
    ],
)
async def test_invented_facts_actions_and_evidence_fall_back(proposal):
    class Model:
        async def chat(self, request, budget):
            return ChatResult(
                structured=proposal, usage=ModelUsage(), finish_reason="stop", model="synthetic"
            )

    original = result()
    reply, presentation = await ReplyComposer(Model()).compose(
        original, ExecutionBudget.start(20, 3, 0)
    )
    assert reply == fact_reply(original)
    assert presentation.mode == "fallback" and presentation.reason == "invalid_output"
    assert "100.00" not in reply and "000042" not in reply and "退款" not in reply


async def test_allowed_wording_preserves_every_fact_exactly():
    class Model:
        async def chat(self, request, budget):
            # Facts and customer identifiers never enter the optional wording prompt.
            assert "99.90" not in request.model_dump_json()
            return ChatResult(
                structured={"opening": "checked", "closing": "help"},
                usage=ModelUsage(),
                finish_reason="stop",
                model="synthetic",
            )

    original = result()
    reply, presentation = await ReplyComposer(Model()).compose(
        original, ExecutionBudget.start(20, 3, 0)
    )
    assert fact_reply(original) in reply and presentation.mode == "polished"
    assert "99.90" in reply and "000031" in reply


@pytest.mark.parametrize("failure", ["timeout", "exception", "cancel", "invalid_schema"])
async def test_optional_model_failure_and_external_cancellation(failure):
    class Model:
        async def chat(self, request, budget):
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "invalid_schema":
                raise AppError("MODEL_OUTPUT_INVALID", "synthetic invalid schema")
            raise RuntimeError("synthetic secret")

    composer = ReplyComposer(Model(), timeout=0.01)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await composer.compose(result(), ExecutionBudget.start(20, 3, 0))
    else:
        reply, presentation = await composer.compose(result(), ExecutionBudget.start(20, 3, 0))
        assert reply == fact_reply(result()) and presentation.mode == "fallback"
        if failure == "invalid_schema":
            assert presentation.reason == "invalid_output"
        assert "secret" not in reply


def test_redaction_is_stable_preserves_scores_and_short_session_does_not_corrupt_text():
    data = {
        "session_id": "s",
        "identity": {"tenant_id": "tenant", "user_id": "alice"},
        "raw_text": "订单000031 未享受优惠 bearer hidden-secret token=hidden-key "
        '{"password":"quoted-private"} 密码：chinese-private',
        "parameters": {"order_id": "000031"},
        "facts": [{"name": "order_id", "value": "000031"}, {"name": "paid", "value": "99.90"}],
        "status": "SUCCEEDED",
        "score": 0.85,
        "secret": "credential",
    }
    redacted = sanitize(data, b"x" * 32)
    text = json.dumps(redacted)
    assert "000031" not in text and "hidden-secret" not in text and "hidden-key" not in text
    assert "quoted-private" not in text and "chinese-private" not in text
    assert "alice" not in text and "credential" not in text
    assert redacted["status"] == "SUCCEEDED" and redacted["score"] == 0.85
    assert redacted["parameters"]["order_id"] == redacted["facts"][0]["value"]
    assert redacted == sanitize(data, b"x" * 32)
    assert redacted != sanitize(data, b"y" * 32)


@pytest.mark.parametrize("text", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "  =1+1"])
def test_export_protects_spreadsheet_formula_strings(text):
    assert json.loads(export_json({"raw_text": text}))["raw_text"] == "'" + text


def test_scope_isolates_all_three_dimensions():
    identity = VerifiedIdentity(tenant_id="t", user_id="u")
    scopes = {
        scope_hash(b"x" * 32, identity, "s"),
        scope_hash(b"x" * 32, identity, "s2"),
        scope_hash(b"x" * 32, identity.model_copy(update={"user_id": "u2"}), "s"),
        scope_hash(b"x" * 32, identity.model_copy(update={"tenant_id": "t2"}), "s"),
    }
    assert len(scopes) == 4


def event(trace, run="run", **changes):
    identity = VerifiedIdentity(tenant_id="t", user_id="u")
    return TraceEvent(
        event="debug_snapshot",
        request_id="request",
        trace_id="trace",
        debug=DebugSnapshot(
            scope_hash=scope_hash(trace.debug_key, identity, "s"),
            run_id=run,
            question_id="q",
            trace_id="trace",
            data={"response": {"reply": "synthetic"}},
        ),
        **changes,
    )


async def test_rotation_reopen_scope_and_expiry(tmp_path):
    path = tmp_path / "trace.jsonl"
    trace = JsonlTrace(path, max_bytes=1300, backups=2, queue_size=100)
    scope = event(trace).debug.scope_hash
    for index in range(12):
        await trace.emit(event(trace, str(index)))
    await trace.aclose()
    assert sum(p.stat().st_size for p in trace._paths() if p.exists()) <= 1300 * 3
    reopened = JsonlTrace(path, max_bytes=1300, backups=2, queue_size=100)
    assert reopened.debug_key == trace.debug_key
    assert (await reopened.read_debug(scope, "11")).run_id == "11"
    assert await reopened.read_debug("0" * 64, "11") is None
    assert await reopened.read_debug(scope, "0") is None
    await reopened.aclose()
    old = datetime.now(UTC) - timedelta(days=8)
    for file in trace._paths():
        if file.exists():
            os.utime(file, (old.timestamp(), old.timestamp()))
    expired = JsonlTrace(path, max_bytes=1300, backups=2)
    assert await expired.read_debug(scope, "11") is None
    await expired.emit(event(expired, "new"))
    await expired.aclose()
    assert '"11"' not in path.read_text(encoding="utf-8")


async def test_trace_queue_disk_full_and_oversize_are_bounded_nonfatal(tmp_path, monkeypatch):
    trace = JsonlTrace(tmp_path / "trace.jsonl", max_bytes=1200, queue_size=2)

    def broken(line):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(trace, "_append", broken)
    for index in range(20):
        await trace.emit(event(trace, str(index)))
    await trace.emit(
        event(trace).model_copy(
            update={"debug": event(trace).debug.model_copy(update={"data": {"raw": "x" * 5000}})}
        )
    )
    await trace.aclose()
    assert trace.dropped >= 19 and trace._queue.qsize() == 0
    assert len(trace._recent) <= 2


def test_free_text_polish_is_never_accepted():
    with pytest.raises(ValidationError):
        apply_wording("实付99.90 CNY。", "实付100 CNY。")


async def test_concurrent_sop_observers_keep_runs_and_cancelled_node_separate():
    reports = {"a": [], "b": []}

    async def observe(name):
        token = sop_node_observer.set(reports[name].append)
        calls = []
        try:
            with observe_sop_node(name, "lookup", calls):
                await asyncio.sleep(0.001)
                calls.append("call-" + name)
                if name == "b":
                    raise asyncio.CancelledError
        except asyncio.CancelledError:
            pass
        finally:
            sop_node_observer.reset(token)

    await asyncio.gather(observe("a"), observe("b"))
    assert reports["a"][0]["node_id"] == "a" and reports["a"][0]["status"] == "completed"
    assert reports["b"][0]["node_id"] == "b" and reports["b"][0]["error_code"] == "CANCELLED"
    assert reports["a"][0]["tool_call_ids"] == ["call-a"]
    assert reports["b"][0]["tool_call_ids"] == ["call-b"]
    assert sop_node_observer.get() is None


@pytest.mark.parametrize("suffix", [".json", ".lock", ".tmp"])
def test_trace_cannot_overwrite_auth_or_control_sidecars(tmp_path, suffix):
    control = tmp_path / "auth.json"
    with pytest.raises(ConfigurationError):
        validate_trace_path(control.with_suffix(suffix), [control])


def test_trace_key_and_controls_are_distinct_without_false_temporary_collision(tmp_path):
    trace = tmp_path / "trace.jsonl"
    validate_trace_path(trace, [tmp_path / "auth.json", tmp_path / "budget.json"])
    with pytest.raises(ConfigurationError):
        validate_trace_path(trace, [trace.with_suffix(".jsonl.key")])
