from datetime import UTC, datetime

import pytest

from deephelp_app.adapters.persistence.history import PostgresHistory
from deephelp_app.application.run_explanation import explain_run
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.history import HistoryMessage
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.conversation import ConverseInput, Question, RequestEnvelope
from deephelp_app.domain.models.responses import DebugSnapshot, ResponseEnvelope, StageReport


def row(response=True):
    return HistoryMessage(
        input=ConverseInput(
            channel="browser",
            session_id="s",
            message_id="m",
            raw_text="物流到哪里了",
            occurred_at=datetime.now(UTC),
        ),
        run_id="r",
        status="SUCCEEDED" if response else "CANCELLED",
        question=Question(
            question_id="q",
            identity=VerifiedIdentity(tenant_id="t", user_id="u"),
            session_id="s",
            version=9,
            status="ACTIVE",
            member_message_ids=("m",),
            versions={},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        response=ResponseEnvelope(
            request_id="req",
            trace_id="original",
            run_id="r",
            outcome="HANDOFF",
            reply="范围外",
            stages=(StageReport(stage="600_INTENT_RECOGNIZE", status="completed"),),
        )
        if response
        else None,
    )


def test_missing_diagnostic_keeps_original_receipt_and_current_version_separate():
    value = explain_run(row(), None, dropped=2)
    assert value["trace_id"] == "original" and value["receipt"]["reply"] == "范围外"
    assert value["question_now"]["version"] == 9 and value["question_at_run"] is None
    assert value["stages"][0]["title"] == "识别意图" and value["stages"][0]["started_at"] is None
    assert value["decision"] is None and value["read_model_calls"] == 0
    assert (
        "debug_snapshot_unavailable" in value["gaps"]
        and "diagnostic_events_dropped" in value["gaps"]
    )


def test_cancelled_without_receipt_never_claims_no_effect_or_cleanup_complete():
    value = explain_run(row(False), None)
    assert value["status"] == "CANCELLED" and value["receipt"] is None
    assert not value["stages"] and "不能推断" in value["summary"]


def test_original_snapshot_is_not_replaced_by_current_question():
    snapshot = DebugSnapshot(
        scope_hash="a" * 64,
        run_id="r",
        question_id="q",
        trace_id="original",
        data={"question": {"version": 2}, "text": {"observations": [{"disposition": "negated"}]}},
        incomplete=("tools_size_limit",),
    )
    value = explain_run(row(), snapshot)
    assert value["question_at_run"]["version"] == 2 and value["question_now"]["version"] == 9
    assert value["entities"]["observations"][0]["disposition"] == "negated"
    assert value["tools"] is None and value["gaps"] == ["tools_size_limit"]


async def test_single_run_query_scopes_both_tables_and_rejects_foreign(monkeypatch):
    stored, owner, calls = row(), VerifiedIdentity(tenant_id="t", user_id="u"), []

    async def read(self, sql, params):
        calls.append((sql, params))
        if params == ("t", "u", "t", "u", "s", "r"):
            request = RequestEnvelope(
                **stored.input.model_dump(),
                request_id="req",
                trace_id="original",
                identity=owner,
                received_at=datetime.now(UTC),
            )
            return [
                (
                    request.model_dump_json(),
                    "r",
                    "SUCCEEDED",
                    stored.question.model_dump_json(),
                    stored.response.model_dump_json(),
                )
            ]
        return []

    monkeypatch.setattr(PostgresHistory, "read", read)
    history = PostgresHistory(None)
    assert (await history.run(owner, "s", "r")).response == stored.response
    for identity, session, identifier in [
        (owner.model_copy(update={"user_id": "other"}), "s", "r"),
        (owner, "foreign", "r"),
        (owner, "s", "missing"),
    ]:
        with pytest.raises(AppError) as error:
            await history.run(identity, session, identifier)
        assert error.value.status_code == 404
    assert (
        "LIMIT 1" in calls[0][0]
        and "m.tenant_id=%s" in calls[0][0]
        and "q.user_id=%s" in calls[0][0]
    )
