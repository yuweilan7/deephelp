"""Offline probe destination and explicit-live guards; no database requests."""

import sys

import pytest

from deephelp_app.adapters.persistence.postgres import PostgresConfig
from deephelp_tools.probes.migration.postgres_probe import main, require_isolated


@pytest.mark.parametrize(
    "database", ["deephelp", "postgres", "langfuse", "deephelp_m25_", "deephelp_m25_x;DROP"]
)
def test_only_explicit_protocol_database(database):
    with pytest.raises(ValueError, match="dedicated"):
        require_isolated(PostgresConfig("localhost", 5432, database, "app", "secret"))


def test_without_live_rejected_before_configuration(monkeypatch, tmp_path):
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["probe", "--config", "missing", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2 and not output.exists()


def test_existing_evidence_cannot_be_overwritten(monkeypatch, tmp_path):
    output = tmp_path / "result.json"
    output.write_text("previous evidence", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["probe", "--live", "--config", "missing", "--output", str(output)]
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2 and output.read_text() == "previous evidence"
