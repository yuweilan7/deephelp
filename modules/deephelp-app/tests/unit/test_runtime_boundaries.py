"""Serving boundaries must work even when the developer tooling is unavailable."""

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from deephelp_app.adapters.asset_integrity import provenance
from deephelp_app.adapters.providers import ProviderConfig
from deephelp_app.application.corpus import digest
from deephelp_app.application.hybrid import ANALYZER
from deephelp_app.application.retrieval_policy import validate_published_selection
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import HybridScope

pytestmark = pytest.mark.unit


def test_business_and_complete_release_import_and_lifespan_without_developer_tools(tmp_path):
    code = r"""
import asyncio
import importlib.abc
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

class NoTools(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ("deephelp_tools",)
        if fullname.startswith(blocked):
            raise AssertionError("Developer tool imported by serving: " + fullname)

sys.meta_path.insert(0, NoTools())
original_open = Path.open
def no_gold(path, *args, **kwargs):
    if any(p in {"datasets", "evaluation", "learning"} for p in path.parts):
        raise AssertionError("Developer data read by serving: " + str(path))
    return original_open(path, *args, **kwargs)
Path.open = no_gold

import httpx
from deephelp_app.bootstrap import mvp_cli
from deephelp_app.bootstrap import release_cli
from deephelp_app.api.app import create_app
from deephelp_app.adapters.asset_integrity import provenance
from deephelp_app.resources import asset_path
from deephelp_app.application.corpus import digest, read_corpus
from deephelp_app.domain.models import HybridScope
from deephelp_app.application.hybrid import ANALYZER
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_app.adapters.providers import ProviderConfig
from deephelp_app.adapters.release_assets import verify_release
from deephelp_app.bootstrap.release_runtime import ReleaseRuntime
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.adapters.synthetic_rights import RightsClient
from deephelp_app.adapters.trace import MemoryTrace

providers = Path("modules/deephelp-app/providers.example.json")
scope = HybridScope(namespace="boundary", dataset_version="boundary-v1",
    index_kind="intent_hybrid", signature=ProviderConfig.load(providers).signature())
corpus = read_corpus(Path("demo/data/m09_corpus.jsonl"))
selection = dict(format="m09-dev-selection-v1", scope=scope.model_dump(mode="json"),
    index_digest=corpus.index_digest, dev_digest="a"*64, test_digest="b"*64,
    candidate_budget=3, analyzer=ANALYZER, dense_weight=0.5)
selection["selection_digest"] = digest(selection)
pointer = Path(sys.argv[1]) / "pointer.json"
pointer.write_text(json.dumps(dict(format="m09-pointer-v1", active=scope.model_dump(mode="json"),
    selection=selection, corpus_digest=corpus.index_digest, dense_weight=0.5)))
assembly = LiveAssembly(Path.cwd(), providers, pointer,
    corpus_path=Path("demo/data/m09_corpus.jsonl"))
assert assembly.top_k == 3
assert provenance()["integrity_scope"] == "runtime-v2"

@asynccontextmanager
async def conversation(client, trace):
    yield SimpleNamespace(cases=None, events=None)

async def check():
    app = create_app(Settings(mode="mvp"), trace=MemoryTrace(),
        identity_provider=lambda request: None, conversation_factory=conversation)
    async with app.router.lifespan_context(app):
        assert app.state.resources.gateway is None
        assert app.state.resources.repository is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1") as client:
            response = await client.get("/health")
            assert response.status_code == 200
asyncio.run(check())
print("business/release imports, hybrid assembly, lifespan and runtime integrity PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout


@pytest.fixture
def selection():
    config = ProviderConfig.load(Path("modules/deephelp-app/providers.example.json"))
    scope = HybridScope(
        namespace="boundary",
        dataset_version="v1",
        index_kind="intent_hybrid",
        signature=config.signature(),
    )
    value = dict(
        format="m09-dev-selection-v1",
        scope=scope.model_dump(mode="json"),
        index_digest="corpus",
        dev_digest="a" * 64,
        test_digest="b" * 64,
        candidate_budget=3,
        analyzer=ANALYZER,
        dense_weight=0.5,
    )
    value["selection_digest"] = digest(value)
    return value, scope


@pytest.mark.parametrize("field", ["scope", "index_digest", "candidate_budget", "analyzer"])
def test_runtime_policy_rejects_modified_publication(selection, field):
    value, scope = selection
    damaged = copy.deepcopy(value)
    damaged[field] = "tampered"
    with pytest.raises(ConfigurationError):
        validate_published_selection(damaged, scope, "corpus", 3)


def test_runtime_policy_keeps_query_provenance_and_rejects_missing_hash(selection):
    value, scope = selection
    validate_published_selection(value, scope, "corpus", 3)
    value.pop("test_digest")
    value["selection_digest"] = digest({k: v for k, v in value.items() if k != "selection_digest"})
    with pytest.raises(ConfigurationError):
        validate_published_selection(value, scope, "corpus", 3)


def test_runtime_integrity_does_not_require_learning_or_frozen_assets(tmp_path, monkeypatch):
    import deephelp_app.adapters.asset_integrity as integrity

    (tmp_path / "runtime.py").write_text("runtime = 1")
    (tmp_path / "assets/evaluation").mkdir(parents=True)
    gold = tmp_path / "assets/evaluation/test.json"
    gold.write_text('{"gold": 1}')
    monkeypatch.setattr(integrity, "PACKAGE", tmp_path)
    first = provenance()["package_digest"]
    gold.unlink()
    assert provenance()["package_digest"] == first
    (tmp_path / "runtime.py").write_text("runtime = 2")
    assert provenance()["package_digest"] != first
