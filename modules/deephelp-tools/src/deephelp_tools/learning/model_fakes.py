"""Replay only explicit synthetic fixtures; default network behavior stays offline."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import httpx

from deephelp_app.adapters.gateway import QianwenGateway
from deephelp_app.adapters.providers import EndpointConfig, ProviderConfig
from deephelp_app.domain.execution import AsyncCalls


class FakeGateway(QianwenGateway):
    def __init__(self, responses: Sequence[dict[str, Any]], *, dimension: int = 2) -> None:
        self._responses = list(responses)
        self.call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            self.call_count += 1
            if not self._responses:
                raise AssertionError("Synthetic fixture responses exhausted")
            return httpx.Response(200, json=self._responses.pop(0))

        endpoint = EndpointConfig(model="synthetic", input_cny_per_million=Decimal("0"))
        config = ProviderConfig(chat=endpoint, embedding=endpoint, dimension=dimension)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        credentials = ("https://synthetic.invalid", "synthetic-key")
        super().__init__(client, AsyncCalls(2, 1, backoff=0), config, credentials, credentials)

    async def aclose(self) -> None:
        await self.client.aclose()
