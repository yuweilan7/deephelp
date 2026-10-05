import json
from pathlib import Path

import pytest

from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.application.artifact_paths import resolve_artifact, state_root
from deephelp_app.application.dense import load_json
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.domain.errors import ConfigurationError


def binding(tmp_path, monkeypatch, rows=None):
    root = tmp_path / "immutable"
    root.mkdir()
    config = tmp_path / "bindings.json"
    config.write_text(json.dumps(rows or [{"source": r"D:\source", "target": str(root)}]))
    monkeypatch.setenv("DEEPHELP_ARTIFACT_MAP", str(config))
    return root


def test_immutable_windows_reference_keeps_bytes_and_hash(tmp_path, monkeypatch):
    root = binding(tmp_path, monkeypatch)
    (root / "assets").mkdir()
    target = root / "assets" / "pointer.json"
    original = b'{"manifest":"D:\\\\source\\\\assets\\\\vectors.json","active":{}}'
    target.write_bytes(original)
    legacy = Path(r"D:\source\assets\pointer.json")
    assert resolve_artifact(legacy) == target
    assert load_json(legacy)["manifest"] == r"D:\source\assets\vectors.json"
    assert file_digest(legacy) == file_digest(target)
    assert target.read_bytes() == original


@pytest.mark.parametrize("value", [r"D:\source\..\secret", r"D:assets\pointer", r"E:\other\file"])
def test_undeclared_and_traversal_references_fail(tmp_path, monkeypatch, value):
    binding(tmp_path, monkeypatch)
    with pytest.raises(ConfigurationError):
        resolve_artifact(value)


def test_overlapping_bindings_are_not_selected_by_guessing(tmp_path, monkeypatch):
    root = tmp_path / "immutable"
    binding(
        tmp_path,
        monkeypatch,
        [
            {"source": r"D:\source", "target": str(root)},
            {"source": r"D:\source\assets", "target": str(root)},
        ],
    )
    with pytest.raises(ConfigurationError, match="exactly one"):
        resolve_artifact(r"D:\source\assets\pointer.json")


def test_host_binding_rejects_symlink_escape(tmp_path, monkeypatch):
    root = binding(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host does not permit test symlinks; verified on Linux deployment")
    with pytest.raises(ConfigurationError, match="escapes"):
        resolve_artifact(r"D:\source\escape\secret")


def test_explicit_state_root_confines_writers(tmp_path, monkeypatch):
    root = tmp_path / "state"
    monkeypatch.setenv("DEEPHELP_STATE_ROOT", str(root))
    assert state_root() == root
    assert local_path(".local/task/budget.json") == root / "task" / "budget.json"
    assert local_path(str(root / "auth.json")) == root / "auth.json"
    with pytest.raises(ConfigurationError):
        local_path(str(tmp_path / "auth.json"))
    with pytest.raises(ConfigurationError):
        local_path(".local/../escape.json")
