import asyncio
import copy
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from deephelp_app.adapters.persistence.ledger import new_receipt
from deephelp_app.adapters.releases.release_assets import release_hash, verify_release
from deephelp_app.adapters.releases.release_store import ReleaseMismatch, ReleaseRepository
from deephelp_app.application.collaboration import load_agent_policy, policy_release_binding
from deephelp_app.application.corpus import digest
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.release import ReleaseManifest, VersionManifest
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_tools.evaluation.quality.core import file_digest
from deephelp_tools.evaluation.releases.release_prepare import prepare_manifest
from deephelp_tools.learning.replay.event_replay import envelope

pytestmark = pytest.mark.unit


def test_prepare_requires_source_provenance_even_when_runtime_hash_is_valid(release, monkeypatch):
    import deephelp_tools.evaluation.releases.release_prepare as preparation

    monkeypatch.setattr(
        preparation,
        "provenance",
        lambda: {"package_digest": "fixed-code", "git_commit": None, "dirty_worktree": None},
    )
    with pytest.raises(ConfigurationError, match="source Git provenance"):
        prepare_manifest(
            Path(release.assets_path), Path(release.providers_path), bundled_registry(), "no-git"
        )


@pytest.fixture
def release(tmp_path, monkeypatch):
    import deephelp_app.adapters.releases.release_assets as runtime_module
    import deephelp_tools.evaluation.releases.release_prepare as module

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
                code_integrity_scope="runtime-v2",
                collaboration_policy_digest=load_agent_policy().digest,
                tooling_digest="fixed-tooling",
                judge_providers_digest=file_digest(tmp_path / "event-judge.example.json"),
                providers_digest=file_digest(paths["providers"]),
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "verify_files", lambda path, **kwargs: copy.deepcopy(data))
    monkeypatch.setattr(
        module, "evaluation_provenance", lambda: dict(tooling_digest="fixed-tooling")
    )
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
    for name in ("verify_files", "pointer_manifest", "FastTextClassifier", "provenance"):
        monkeypatch.setattr(runtime_module, name, getattr(module, name))
    return prepare_manifest(paths["assets"], paths["providers"], bundled_registry(), "complete-v1")


def test_complete_manifest_binds_all_routes_and_code(release):
    restored = ReleaseManifest.model_validate_json(release.model_dump_json())
    assert release_hash(restored) == release_hash(release)
    assert verify_release(restored)
    assert restored.fasttext["preprocessing_signature"] == "dict-v1"
    assert restored.sop_snapshot == bundled_registry().snapshot_hash
    assert restored.prompts and restored.code_digest == "fixed-code"


def test_new_optional_skill_fields_preserve_original_release_identity(release):
    # 独立按旧DTO字段计算原hash，不能用release_hash自己断言自己。
    raw = release.model_dump(mode="json")
    raw.pop("skill_registry")
    raw.pop("skill_review_path")
    raw.pop("sop_review_path")
    assert release_hash(ReleaseManifest.model_validate(raw)) == digest(raw)


def test_approved_sop_requires_exact_new_content_and_immutable_review(release, tmp_path):
    """固定门禁依赖只验机制；旧PASS不能覆盖新SOP，原件/完整闭包须双向绑定。"""
    from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, read_sop_review
    from deephelp_app.domain.models.common import VerifiedIdentity

    base = bundled_registry()
    owner = VerifiedIdentity(tenant_id="test", user_id="a")
    store = SOPDraftStore(tmp_path / "sop")
    draft = store.create(owner, base, base.definitions[0].sop_id)
    draft["content"]["version"] = "reviewed-order-v43"
    saved = store.update(draft["draft_id"], owner, 1, draft["content"])
    store.review(saved["draft_id"], owner, 2, base, "明确核对字段差异", True)
    reviewed = store.approved(saved["draft_id"], owner)
    candidate, _ = read_sop_review(reviewed)
    with pytest.raises(ConfigurationError, match="SOP/code/providers"):
        prepare_manifest(
            Path(release.assets_path),
            Path(release.providers_path),
            candidate,
            "old-report",
            sop_review=reviewed,
        )
    report = Path(release.validation_path)
    value = json.loads(report.read_text())
    value.update(sop_snapshot=candidate.snapshot_hash, sop_review_digest=file_digest(reviewed))
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="approved artifact"):
        prepare_manifest(
            Path(release.assets_path), Path(release.providers_path), candidate, "absent"
        )
    complete = prepare_manifest(
        Path(release.assets_path),
        Path(release.providers_path),
        candidate,
        "sop-v43",
        sop_review=reviewed,
    )
    assert verify_release(complete)
    omitted = complete.model_copy(update={"sop_review_path": None})
    with pytest.raises(ConfigurationError, match="immutable review"):
        verify_release(omitted)
    altered = json.loads(reviewed.read_text())
    altered["reason"] = "审核原件后来被替换"
    reviewed.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="artifact changed"):
        verify_release(complete)


def test_approved_skill_snapshot_exact_content_and_file_binding(release, tmp_path):
    from deephelp_app.adapters.releases.skill_store import SkillStore
    from deephelp_app.application.skills import load_skills
    from deephelp_app.domain.models.common import VerifiedIdentity

    registry = load_skills()
    store = SkillStore(tmp_path / "skills")
    store.propose(registry, registry)
    store.review(
        registry.digest,
        1,
        VerifiedIdentity(tenant_id="test", user_id="a"),
        "契约核对通过，固定内容证据在validation",
        True,
    )
    reviewed = store.approved(registry.digest)
    report = Path(release.validation_path)
    value = json.loads(report.read_text())
    value.update(skills_digest=registry.digest, skills_review_digest=file_digest(reviewed))
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="approved artifact"):
        prepare_manifest(
            Path(release.assets_path), Path(release.providers_path), bundled_registry(), "bad"
        )
    complete = prepare_manifest(
        Path(release.assets_path),
        Path(release.providers_path),
        bundled_registry(),
        "skills-v1",
        skill_review=reviewed,
    )
    assert verify_release(complete)
    assert complete.skill_registry == registry.content()
    assert complete.prompts["skills:registry"] == registry.digest
    assert str(reviewed.resolve()) in complete.files
    for altered in (
        complete.model_copy(update={"skill_review_path": None}),
        complete.model_copy(update={"skill_registry": None}),
        complete.model_copy(update={"skill_registry": {**registry.content(), "version": "drift"}}),
        complete.model_copy(
            update={
                "files": {k: v for k, v in complete.files.items() if k != str(reviewed.resolve())}
            }
        ),
    ):
        with pytest.raises(ConfigurationError):
            verify_release(altered)
    # 审核原因改动同样改变原件hash，不能借审批状态为APPROVED而通过。
    reviewed.write_text(reviewed.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="artifact changed"):
        verify_release(complete)


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
    import deephelp_app.bootstrap.runtime.release_runtime as module
    from deephelp_app.adapters.mcp.business.tool_gateway import process_alive
    from deephelp_tools.demo.assembly import DemoToolGateway as ToolGateway

    owners, gateways, configurations = [], [], []

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
            configurations.append(kwargs)

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
    from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig

    business = HTTPMCPConfig(url="http://127.0.0.1:8011/mcp", credentials_path=Path("business"))
    retrieval = HTTPMCPConfig(
        url="http://127.0.0.1:8012/mcp", credentials_path=Path("retrieval"), audience="retrieval"
    )
    runtime = module.ReleaseRuntime("unit", business_mcp=business, retrieval_mcp=retrieval)
    async with runtime.open(None, None) as router:
        await asyncio.create_task(router.entry("new-from-request-task"))
    assert len(owners) == 2 and all(enter is exit for enter, exit in owners)
    assert all(tool.pid and not process_alive(tool.pid) for tool in gateways)
    assert all(
        c["business_mcp"] is business and c["retrieval_mcp"] is retrieval for c in configurations
    )
    assert runtime.tools is gateways[0]


async def test_continuation_cannot_accept_a_half_new_release(release, monkeypatch):
    from deephelp_app.adapters.approval.approval_store import ApprovalRepository

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


def test_platform_prompt_binds_local_version_and_exact_validated_requests(release, tmp_path):
    from deephelp_app.application.intent_prompt import (
        ROLE,
        PromptArtifact,
        default_system,
        fallback_contract,
        published_prompt,
    )

    artifact = PromptArtifact(
        name="deephelp-intent-fallback",
        version=1,
        text=default_system(),
        contract=fallback_contract(),
        request_digest=digest([default_system(), fallback_contract()]),
    )
    path = tmp_path / "prompt-v1.json"
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="request changed"):
        prepare_manifest(
            Path(release.assets_path),
            Path(release.providers_path),
            bundled_registry(),
            "prompt-a",
            prompt_artifact=path,
        )
    validation_path = Path(release.validation_path)
    validation = json.loads(validation_path.read_text())
    validation["intent_prompt_request_digest"] = artifact.request_digest
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    bound = prepare_manifest(
        Path(release.assets_path),
        Path(release.providers_path),
        bundled_registry(),
        "prompt-a",
        prompt_artifact=path,
    )
    assert bound.prompts[ROLE] == file_digest(path)
    assert verify_release(bound) and published_prompt(bound)[1] == artifact
    # A second immutable platform version with byte-identical requests can reuse
    # one exact content observation; provenance remains different in the manifest.
    path2 = tmp_path / "prompt-v2.json"
    path2.write_text(artifact.model_copy(update={"version": 2}).model_dump_json(), encoding="utf-8")
    other = prepare_manifest(
        Path(release.assets_path),
        Path(release.providers_path),
        bundled_registry(),
        "prompt-b",
        prompt_artifact=path2,
    )
    assert release_hash(other) != release_hash(bound) and verify_release(other)
    assert published_prompt(bound)[1].version == 1 and published_prompt(other)[1].version == 2
    changed = artifact.model_copy(
        update={"text": "不同语义", "request_digest": digest(["不同语义", fallback_contract()])}
    )
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="request changed"):
        prepare_manifest(
            Path(release.assets_path),
            Path(release.providers_path),
            bundled_registry(),
            "changed",
            prompt_artifact=changed_path,
        )
    validation["intent_prompt_request_digest"] = changed.request_digest
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="requires its immutable artifact"):
        prepare_manifest(
            Path(release.assets_path),
            Path(release.providers_path),
            bundled_registry(),
            "omitted-custom",
        )
    path.write_text(path2.read_text(), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="artifact changed"):
        verify_release(bound)


def test_complete_release_explicitly_binds_agent_role_policy_asset(release):
    binding = policy_release_binding()
    assert release.prompts[binding["resource"]] == binding["sha256"]
    validation = Path(release.validation_path)
    body = json.loads(validation.read_text(encoding="utf-8"))
    body["collaboration_policy_digest"] = "sha256:changed-policy"
    validation.write_text(json.dumps(body), encoding="utf-8")
    # 报告即使标PASS并重算文件摘要，角色/许可实际绑定变化仍不能发布。
    changed = release.model_copy(
        update={
            "files": release.files | {str(validation.resolve()): file_digest(validation)},
            "validation_digest": digest(body),
        }
    )
    with pytest.raises(ConfigurationError, match="Agent role/policy"):
        verify_release(changed)
