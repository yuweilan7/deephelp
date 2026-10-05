"""Owned fixed-protocol children close even when the second database open fails."""

import json
from argparse import Namespace
from unittest.mock import AsyncMock

import pytest

from deephelp_tools.probes.approval import approval_probe as probe


async def test_matrix_database_open_failure_stops_owned_rights_process(tmp_path, monkeypatch):
    process = object()
    start = AsyncMock(return_value=process)
    stop = AsyncMock()
    monkeypatch.setattr(probe, "start_rights", start)
    monkeypatch.setattr(probe, "stop", stop)
    monkeypatch.setattr(probe.ApprovalRepository, "open", AsyncMock(side_effect=TimeoutError()))
    args = Namespace(
        live=True,
        fixed_only=True,
        stage="feature",
        output=tmp_path / "report.json",
        key=tmp_path / "rights.key",
    )
    with pytest.raises(TimeoutError):
        await probe.matrix(args)
    stop.assert_awaited_once_with(process)
    assert json.loads(args.output.read_text())["status"] == "FAIL"
