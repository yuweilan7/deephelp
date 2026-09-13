import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from deephelp_app.domain.models import (
    ConverseInput,
    Outcome,
    RequestEnvelope,
    ResponseEnvelope,
    VerifiedIdentity,
)
from deephelp_app.errors import ConfigurationError
from deephelp_app.fakes import FakeRepository
from deephelp_app.settings import Settings

pytestmark = pytest.mark.unit


def test_dtos_are_json_serializable_and_unknown_versions_are_null(message):
    envelope = RequestEnvelope(
        **message,
        identity=VerifiedIdentity(tenant_id="tenant-a", user_id="user-a"),
        request_id="req-a",
        trace_id="trace-a",
        received_at=datetime.now(UTC),
    )
    assert RequestEnvelope.model_validate_json(envelope.model_dump_json()) == envelope
    response = ResponseEnvelope(
        request_id="req-a", trace_id="trace-a", outcome=Outcome.ERROR, reply="stub"
    )
    data = json.loads(response.model_dump_json())
    assert data["run_id"] is None
    assert data["versions"]["model"] is None
    assert data["versions"]["embedding_signature"] is None
    assert data["budget_used"]["tokens"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "spoofed"),
        ("user_id", "spoofed"),
        ("run_id", "old-run"),
        ("raw_text", "   "),
        ("raw_text", ""),
        ("message_id", "bad id"),
        ("occurred_at", "2026-09-13T01:00:00"),
    ],
)
def test_untrusted_input_cannot_inject_identity_or_execution_ids(message, field, value):
    with pytest.raises(ValidationError):
        ConverseInput.model_validate({**message, field: value})


def test_controlled_environment_and_redacted_settings():
    settings = Settings.from_env(
        {
            "DEEPHELP_MODEL_API_KEY": "synthetic-secret",
            "DEEPHELP_MODEL_BASE_URL": "https://synthetic.invalid/v1",
            "DEEPHELP_MODE": "test",
        }
    )
    assert settings.model_api_key.get_secret_value() == "synthetic-secret"
    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-secret" not in settings.model_dump_json()
    assert "synthetic.invalid" not in repr(settings)
    assert Settings.from_env({}).model_api_key is None
    with pytest.raises(ConfigurationError, match="request_timeout") as error:
        Settings.from_env({"DEEPHELP_REQUEST_TIMEOUT": "synthetic-secret"})
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"mode": "live"}, "ENABLE_LIVE"),
        ({"mode": "live", "enable_live": True}, "MODEL_API_KEY"),
        (
            {"mode": "live", "enable_live": True, "model_api_key": SecretStr("fake")},
            "MODEL_BASE_URL",
        ),
        (
            {
                "mode": "live",
                "enable_live": True,
                "model_api_key": SecretStr("fake"),
                "model_base_url": "https://synthetic.invalid",
            },
            "LIVE_MODEL",
        ),
        (
            {
                "mode": "live",
                "enable_live": True,
                "model_api_key": SecretStr("fake"),
                "model_base_url": "https://synthetic.invalid",
                "live_model": "fake",
            },
            "budgets",
        ),
    ],
)
def test_live_requires_identity_target_and_budget(values, expected):
    with pytest.raises(ConfigurationError, match=expected):
        Settings(**values).validate_live()


async def test_fake_repository_scope_and_read_isolation(message):
    repository = FakeRepository()
    identity = VerifiedIdentity(tenant_id="tenant-a", user_id="user-a")
    request = RequestEnvelope(
        **message, identity=identity, request_id="r", trace_id="t", received_at=datetime.now(UTC)
    )
    response = ResponseEnvelope(request_id="r", trace_id="t", outcome=Outcome.ERROR, reply="fake")
    await repository.put(request, response)
    assert await repository.get(identity, "local", "message-1") == response
    assert await repository.get(identity, "another-channel", "message-1") is None
    assert (
        await repository.get(
            VerifiedIdentity(tenant_id="tenant-b", user_id="user-a"), "local", "message-1"
        )
        is None
    )
    assert (
        await repository.get(
            VerifiedIdentity(tenant_id="tenant-a", user_id="user-b"), "local", "message-1"
        )
        is None
    )
    copy = await repository.get(identity, "local", "message-1")
    copy.facts.append({"synthetic": "mutation"})
    assert (await repository.get(identity, "local", "message-1")).facts == []
