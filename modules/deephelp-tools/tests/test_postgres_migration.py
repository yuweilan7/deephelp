"""Offline preservation boundaries; real import/resume/restore has an explicit entry."""

import gzip
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from deephelp_tools.evaluation.migration.postgres_migration import (
    canonical,
    identifier,
    read_snapshot,
    typed,
    untyped,
)


@pytest.mark.parametrize(
    "value,column",
    [
        (None, "varchar(32)"),
        ("000031", "varchar(128)"),
        ("CaseSensitive  ", "varchar(128)"),
        ('{"z": 1, "a": 2, "a": 3}', "json"),
        (b"\x00\xff\x80typed-checkpoint", "longblob"),
        (Decimal("173.45"), "decimal(20,2)"),
        (0, "bigint unsigned"),
        (2**63 - 1, "bigint unsigned"),
        (datetime(2026, 10, 4, 1, 2, 3, 123456, tzinfo=UTC), "datetime(6)"),
    ],
)
def test_round_trip_preserves_original_values_and_bytes(value, column):
    assert untyped(typed(value, column)) == value


@pytest.mark.parametrize("value,column", [(2**63, "bigint unsigned"), (2, "tinyint(1)")])
def test_target_range_is_explicit(value, column):
    with pytest.raises(ValueError):
        typed(value, column)


@pytest.mark.parametrize("name", ["../../secret", "Table", "x;DELETE", "public.x"])
def test_snapshot_names_cannot_be_sql(name):
    with pytest.raises(ValueError):
        identifier(name)


def test_interrupted_snapshot_is_rejected_before_import(tmp_path, monkeypatch):
    import deephelp_tools.evaluation.migration.postgres_migration as migration

    monkeypatch.setattr(migration, "application_tables", lambda: {"dh_test"})
    path = tmp_path / "snapshot.gz"
    header = {
        "format": "deephelp-m25-snapshot-v1",
        "read_only": True,
        "tables": [
            {"name": "dh_test", "columns": [["id", "int"]]},
            {"name": "p00_probe", "columns": [["id", "int"]]},
        ],
    }
    with gzip.open(path, "wb") as stream:
        stream.write(canonical(header) + b"\n")
    with pytest.raises(ValueError, match="did not complete"):
        read_snapshot(path)


def test_completed_snapshot_detects_changed_row(tmp_path, monkeypatch):
    import deephelp_tools.evaluation.migration.postgres_migration as migration

    monkeypatch.setattr(migration, "application_tables", lambda: {"dh_test"})
    path = tmp_path / "snapshot.gz"
    header = {
        "format": "deephelp-m25-snapshot-v1",
        "read_only": True,
        "tables": [
            {"name": "dh_test", "columns": [["id", "int"]]},
            {"name": "p00_probe", "columns": [["id", "int"]]},
        ],
    }
    with gzip.open(path, "wb") as stream:
        for item in (
            header,
            {"table": "dh_test", "values": [["int", 2]]},
            {"complete": {"tables": {"dh_test": {"rows": 1, "sha256": "wrong"}}}},
        ):
            stream.write(canonical(item) + b"\n")
    with pytest.raises(ValueError, match="checksum"):
        read_snapshot(path)
