import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from deephelp_app.corpus import digest
from deephelp_app.domain.models import ReleaseManifest, ResponseEnvelope, VersionManifest
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation import file_digest
from deephelp_app.event_replay import envelope
from deephelp_app.ledger import new_receipt
from deephelp_app.release_assets import prepare_manifest, release_hash, verify_release
from deephelp_app.release_store import ReleaseMismatch, ReleaseRepository
from deephelp_app.sop_governance import bundled_registry

pytestmark = pytest.mark.unit


@pytest.fixture
def release(tmp_path, monkeypatch):
    import deephelp_app.release_assets as module

    paths = {
        name: tmp_path / (name + ".json")
        for name in ("dense", "fasttext", "rules", "assets", "providers")
    }
    signature = dict(model="frozen-embedding", dimension=1024)
    for path in paths.values():
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "event-judge.example.json").write_text("{}", encoding="utf-8")
    paths["dense"].write_text(
        json.dumps(dict(corpus_digest="corpus-v1", active=dict(signature=signature))),
        encoding="utf-8",
    )
    files = {str(path.resolve()): file_digest(path) for path in paths.values()}
    source = dict(candidates={}, digest=digest({}))
    data = dict(
        files=files,
        dense_pointer=str(paths["dense"]),
        fasttext_pointer=str(paths["fasttext"]),
        rules=str(paths["rules"]),
        providers_digest=file_digest(paths["providers"]),
        source=source,
    )
    (tmp_path / "validation.json").write_text(
        json.dumps(
            dict(
                status="PASS",
                assets_digest=digest(data),
                sop_snapshot=bundled_registry().snapshot_hash,
                code_digest="fixed-code",
                judge_providers_digest=file_digest(tmp_path / "event-judge.example.json"),
                providers_digest=file_digest(paths["providers"]),
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "verify_files", lambda path: copy.deepcopy(data))
    monkeypatch.setattr(module, "pointer_manifest", lambda path: path)
    monkeypatch.setattr(
        module,
        "FastTextClassifier",
        lambda path: SimpleNamespace(
            manifest=SimpleNamespace(
                model_dump=lambda **kwargs: dict(version="ft-v1", preprocessing_signature="dict-v1")
            )
        ),
    )
    monkeypatch.setattr(
        module,
        "provenance",
        lambda: dict(package_digest="fixed-code", git_commit="abc", dirty_worktree=False),
    )
    return prepare_manifest(paths["assets"], paths["providers"], bundled_registry(), "complete-v1")


def test_complete_manifest_binds_all_routes_and_code(release):
    restored = ReleaseManifest.model_validate_json(release.model_dump_json())
    assert release_hash(restored) == release_hash(release)
    assert verify_release(restored)
    assert restored.fasttext["preprocessing_signature"] == "dict-v1"
    assert restored.sop_snapshot == bundled_registry().snapshot_hash
    assert restored.prompts and restored.code_digest == "fixed-code"


@pytest.mark.parametrize(
    "field",
    [
        "corpus_digest",
        "embedding_signature",
        "fasttext",
        "rules_digest",
        "sop_snapshot",
        "providers_digest",
        "judge_providers_digest",
        "code_digest",
        "review_snapshot",
        "validation_digest",
    ],
)
def test_mixed_release_versions_are_rejected_before_use(release, field):
    value = dict(changed=True) if isinstance(getattr(release, field), dict) else "changed"
    damaged = release.model_copy(update={field: value})
    with pytest.raises(ConfigurationError):
        verify_release(damaged)


def test_missing_fingerprint_and_changed_file_fail_closed(release):
    files = dict(release.files)
    rules = next(p for p in files if Path(p).name == "rules.json")
    files.pop(rules)
    with pytest.raises(ConfigurationError, match="fingerprint"):
        verify_release(release.model_copy(update={"files": files}))
    Path(rules).write_text('{"tampered":true}', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="artifact changed"):
        verify_release(release)


def test_static_valid_sop_cannot_bypass_its_content_regression(release):
    registry = bundled_registry().model_copy(update={"registry_version": "different-registry"})
    changed = release.model_copy(
        update={
            "sop_registry": registry.model_dump(mode="json"),
            "sop_snapshot": registry.snapshot_hash,
        }
    )
    with pytest.raises(ConfigurationError, match="do not match"):
        verify_release(changed)
    with pytest.raises(ConfigurationError, match="regression must match"):
        prepare_manifest(Path(release.assets_path), Path(release.providers_path), registry, "new")


async def test_dynamic_mcp_assembly_is_closed_in_its_owner_task(release, monkeypatch):
    import deephelp_app.release_runtime as module
    from deephelp_app.tool_gateway import ToolGateway, process_alive

    owners, gateways = [], []

    class Store:
        @classmethod
        async def open(cls, root):
            return cls()

        async def active(self, channel):
            return SimpleNamespace(active="initial")

        async def manifest(self, identity):
            return release

        async def aclose(self):
            pass

    class Assembly:
        def __init__(self, *args, **kwargs):
            self.tools = ToolGateway()
            gateways.append(self.tools)

        @asynccontextmanager
        async def open(self, client, trace):
            owner = asyncio.current_task()
            async with self.tools.open():
                yield SimpleNamespace(ledger=ReleaseRepository(None))
                owners.append((owner, asyncio.current_task()))

    monkeypatch.setattr(module, "ReleaseStore", Store)
    monkeypatch.setattr(module, "LiveAssembly", Assembly)
    monkeypatch.setattr(
        module,
        "verify_release",
        lambda manifest: dict(dense_pointer="unused", fasttext_pointer="unused", rules="unused"),
    )
    runtime = module.ReleaseRuntime("unit")
    async with runtime.open(None, None) as router:
        await asyncio.create_task(router.entry("new-from-request-task"))
    assert len(owners) == 2 and all(enter is exit for enter, exit in owners)
    assert all(tool.pid and not process_alive(tool.pid) for tool in gateways)


async def test_continuation_cannot_accept_a_half_new_release(release, monkeypatch):
    from deephelp_app.approval_store import ApprovalRepository

    request = envelope("订单000031优惠未到账", "release-test")
    original = new_receipt(request)
    original = original.__class__(
        True,
        original.run_id,
        original.question.model_copy(
            update={"versions": VersionManifest(release_manifest="old-complete")}
        ),
    )

    async def previous(*args):
        return original

    monkeypatch.setattr(ApprovalRepository, "prepare_question", previous)
    repo = ReleaseRepository(None)
    repo.manifest = release
    with pytest.raises(ReleaseMismatch) as exc:
        await repo.prepare_question(None, request, original)
    assert exc.value.expected == "old-complete"


async def test_terminal_write_cannot_publish_mixed_release(release):
    repo = ReleaseRepository(None)
    repo.manifest = release
    request = envelope("测试", "mixed-release")
    receipt = new_receipt(request)
    question = receipt.question.model_copy(
        update={"versions": VersionManifest(release_manifest=release_hash(release))}
    )
    response = ResponseEnvelope(
        request_id=request.request_id,
        trace_id=request.trace_id,
        outcome="HANDOFF",
        reply="测试",
        versions=VersionManifest(release_manifest="foreign"),
    )
    with pytest.raises(ConfigurationError, match="mixed complete release"):
        await repo.before_finish(None, receipt, question, response)
