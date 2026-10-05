from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.learning.workbench.analysis import analysis_router

pytestmark = pytest.mark.integration


async def test_rule_provenance_and_shared_budget_context():
    identity = VerifiedIdentity(tenant_id="t", user_id="u")
    seen = []

    class Result:
        def model_dump(self, **kwargs):
            return {"candidates": []}

    class Retriever:
        diagnostics = []

        async def compare(self, text, scope, budget, *, top_k):
            context = request_execution.get()
            assert context.identity == identity and context.raw_text == text
            assert top_k == 3 and budget is seen[0]
            self.diagnostics.append({"request_id": context.request_id, "model_calls": 0})
            return {mode: Result() for mode in ("dense", "bm25", "hybrid")}

    def gate():
        budget = ExecutionBudget.start(90, 4, 0)
        seen.append(budget)
        return budget

    app = create_app(Settings(mode="test"), trace=MemoryTrace(), budget_factory=gate)
    assembly = SimpleNamespace(retrieval=Retriever(), scope="owned")
    app.include_router(analysis_router(assembly, lambda r: identity))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        text = "不是订单90220000，是订单90221004，优惠未到账"
        d = (await c.post("/learning/rules", json={"text": text})).json()
        assert d["model_calls"] == 0 and len(seen) == 0
        assert any(e["disposition"] == "negated" for e in d["observations"])
        assert d["confirmed"][0]["value"] == "90221004"
        for observation in d["observations"]:
            assert (
                text[observation["start"] : observation["end"]] == observation["source"]["excerpt"]
            )
        assert (
            await c.post("/learning/retrieval", json={"text": "query", "scope": "foreign"})
        ).status_code == 422
        d = (await c.post("/learning/retrieval", json={"text": "query"})).json()
        assert set(d["routes"]) == {"dense", "bm25", "hybrid"} and len(seen) == 1
        assert request_execution.get() is None
