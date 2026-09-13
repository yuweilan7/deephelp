import httpx
import pytest

from deephelp_app.app import create_app
from deephelp_app.settings import Settings

pytestmark = pytest.mark.e2e


async def test_offline_api_writes_trace_and_publishes_placeholder_schema(tmp_path, message):
    """Application wiring only. This is not business Agent or infrastructure e2e."""
    trace_path = tmp_path / "logs" / "trace.jsonl"
    app = create_app(Settings(mode="test", trace_path=str(trace_path)))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["capability"] == "NOT_IMPLEMENTED"
            schema = (await client.get("/openapi.json")).json()
            assert schema["paths"]["/converse"]["post"]["responses"]["501"]
            response = await client.post("/converse", json=message)
            body = response.json()
            assert response.status_code == 501
            assert body["outcome"] == "ERROR"
            assert body["error"]["code"] == "NOT_IMPLEMENTED"
            assert body["facts"] == body["evidence_refs"] == []
            assert body["request_id"] == response.headers["x-request-id"]
    assert trace_path.exists()
    assert message["raw_text"] not in trace_path.read_text(encoding="utf-8")
