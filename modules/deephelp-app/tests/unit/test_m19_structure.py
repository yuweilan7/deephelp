"""Independent runtime and explicit composition contracts introduced by M19."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import deephelp_app
from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.resources import asset_path

pytestmark = pytest.mark.unit
PACKAGE = Path(deephelp_app.__file__).parent


def test_runtime_has_no_tools_or_service_import_and_domain_has_no_outer_layer_import():
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert all(
                not name.startswith(("deephelp_tools", "deephelp_services")) for name in names
            ), path
            if "domain" in path.parts:
                assert all(
                    not name.startswith("deephelp_app.") or name.startswith("deephelp_app.domain")
                    for name in names
                ), path


@pytest.mark.parametrize("name", ["business.json", "m17_cases.json", "m09_corpus.jsonl"])
def test_runtime_resource_lookup_cannot_silently_read_demo_or_gold(name):
    with pytest.raises(ValueError, match="runtime resource"):
        asset_path(name)


async def test_unselected_mcp_is_rejected_before_starting_a_process():
    gateway = ToolGateway()
    with pytest.raises(ConfigurationError, match="explicit MCP"):
        async with gateway.open():
            pytest.fail("Unselected backend must never run")
    assert gateway.pid is None


async def test_unselected_runtime_rejects_before_any_dependency_is_opened(monkeypatch):
    assembly = object.__new__(LiveAssembly)
    assembly.tool_server = None
    open_database = AsyncMock(
        side_effect=AssertionError("Database opened before backend selection")
    )
    monkeypatch.setattr(
        "deephelp_app.bootstrap.mvp_runtime.MySQLCaseRepository.open", open_database
    )
    with pytest.raises(ConfigurationError, match="explicit MCP"):
        async with assembly.open(None, None):
            pytest.fail("Unselected runtime must never run")
    open_database.assert_not_called()


def test_installed_runtime_integrity_without_git_still_binds_content(tmp_path, monkeypatch):
    from deephelp_app.adapters import asset_integrity

    source = tmp_path / "runtime.py"
    source.write_text("version = 1")
    monkeypatch.setattr(asset_integrity, "PACKAGE", tmp_path)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("Git is not installed")

    monkeypatch.setattr(asset_integrity.subprocess, "run", no_git)
    before = asset_integrity.provenance()
    assert before["git_commit"] is None
    assert before["dirty_worktree"] is None
    source.write_text("version = 2")
    assert asset_integrity.provenance()["package_digest"] != before["package_digest"]
