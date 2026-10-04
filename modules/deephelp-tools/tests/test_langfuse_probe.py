"""Evidence guards and content counterexamples; SDK/storage live checks use the explicit CLI."""

import json
import sys
from copy import deepcopy

import pytest

from deephelp_tools.probes.langfuse_probe import check_trace, configuration, main


@pytest.mark.parametrize(
    "host",
    [
        "https://cloud.langfuse.com",
        "http://remote:3000",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1?key=secret",
    ],
)
def test_platform_probe_rejects_non_private_or_embedded_secrets(tmp_path, host):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"host": host, "s3_endpoint": "http://127.0.0.1:18333", "bucket": "langfuse"})
    )
    with pytest.raises(ValueError):
        configuration(path)


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--live", "--count", "101"],
        ["--live", "--timeout", "9999"],
        ["--live", "--mode", "verify"],
    ],
)
def test_gate_rejects_before_opening_credentials(tmp_path, monkeypatch, flags):
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "--config", "absent", "--output", str(tmp_path / "result.json"), *flags],
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2


def test_report_cannot_overwrite_prior_evidence(tmp_path, monkeypatch):
    output = tmp_path / "result.json"
    output.write_text("original")
    monkeypatch.setattr(
        sys, "argv", ["probe", "--live", "--config", "absent", "--output", str(output)]
    )
    with pytest.raises(SystemExit):
        main()
    assert output.read_text() == "original"


@pytest.fixture
def stored_trace():
    expected = {
        "trace_id": "trace",
        "root_id": "root",
        "observation_id": "generation",
        "score_id": "score",
    }
    body = {
        "observations": [
            {
                "id": "root",
                "traceId": "trace",
                "isRootObservation": True,
                "parentObservationId": "sdk-physical-parent",
                "name": "m27-platform-proof",
                "output": '{"amount":"173.45","synthetic":true}',
            },
            {
                "id": "generation",
                "traceId": "trace",
                "name": "m27-synthetic-generation",
                "output": "Synthetic amount 173.45",
                "usageDetails": {"input": 17, "output": 9, "total": 26},
                "parentObservationId": "root",
            },
        ],
        "scores": [
            {
                "id": "score",
                "value": 1,
                "subject": {"kind": "observation", "id": "generation", "traceId": "trace"},
            }
        ],
    }
    return body, expected


@pytest.mark.parametrize(
    "defect", ["identity", "name", "content", "usage", "score", "association", "parent", "root"]
)
def test_successful_http_cannot_hide_content_or_association_defect(stored_trace, defect):
    original, expected = stored_trace
    body = deepcopy(original)
    assert check_trace(body, expected)["parent_id"] == "root"
    if defect == "identity":
        body["observations"][1]["traceId"] = "another-trace"
    elif defect in {"name", "content", "usage"}:
        body["observations"][1][
            {"name": "name", "content": "output", "usage": "usageDetails"}[defect]
        ] = {"input": 0, "output": 0} if defect == "usage" else "wrong"
    elif defect == "parent":
        body["observations"][1]["parentObservationId"] = "other-parent"
    elif defect == "root":
        body["observations"][0]["isRootObservation"] = False
    elif defect == "association":
        body["scores"][0]["subject"]["id"] = "other-observation"
    else:
        body["scores"][0]["value"] = 0
    with pytest.raises(AssertionError):
        check_trace(body, expected)
