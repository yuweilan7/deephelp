"""Explicit service credentials and short-lived, audience-bound delegation for HTTP MCP."""

import base64
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import RequestParamsMeta
from pydantic import Field, SecretStr

from deephelp_app.adapters.mcp_protocol import sign_metadata, verify_metadata
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import DTO, ErrorCode


class ServiceCredentials(DTO):
    bearer: SecretStr
    delegation_key: SecretStr

    @classmethod
    def load(cls, path: Path) -> ServiceCredentials:
        value = cls.model_validate_json(path.read_bytes())
        for secret in (value.bearer, value.delegation_key):
            raw = secret.get_secret_value()
            if len(raw) < 43 or not raw.isascii():
                raise ConfigurationError("Service credentials need separate random 256-bit secrets")
        if value.bearer == value.delegation_key:
            raise ConfigurationError("Service transport and delegation secrets must differ")
        return value

    def protect(self, audience: str, payload: dict[str, Any]) -> RequestParamsMeta:
        return sign_metadata(
            self.delegation_key.get_secret_value(),
            {**payload, "audience": audience, "expires_at": time.time() + 60},
        )


class HTTPMCPConfig(DTO):
    url: str
    credentials_path: Path = Field(exclude=True)
    audience: str = "business"

    def credentials(self) -> ServiceCredentials:
        target = urlsplit(self.url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username
            or target.password
            or target.query
            or target.fragment
            or (
                target.scheme == "http" and target.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
            or target.path != "/mcp"
        ):
            raise ConfigurationError("MCP requires /mcp and TLS outside loopback")
        return ServiceCredentials.load(self.credentials_path)


class DelegationVerifier:
    def __init__(self, credentials: ServiceCredentials, audience: str) -> None:
        self.credentials, self.audience = credentials, audience
        self.nonces: dict[str, float] = {}

    def verify(self, meta: RequestParamsMeta | None) -> dict[str, Any]:
        payload = verify_metadata(self.credentials.delegation_key.get_secret_value(), meta)
        expires, nonce = payload.get("expires_at"), payload.get("nonce")
        now = time.time()
        if (
            payload.get("audience") != self.audience
            or not isinstance(expires, (float, int))
            or isinstance(expires, bool)
            or not now < expires <= now + 65
            or not isinstance(nonce, str)
            or not 1 <= len(nonce) <= 128
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Invalid service delegation")
        self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry > now}
        if nonce in self.nonces:
            raise AppError(ErrorCode.FORBIDDEN, "Replayed service delegation")
        if len(self.nonces) >= 10000:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Service delegation capacity exhausted")
        self.nonces[nonce] = float(expires)
        return {k: v for k, v in payload.items() if k not in {"audience", "expires_at"}}


def encode_delegation(meta: RequestParamsMeta) -> str:
    return base64.urlsafe_b64encode(json.dumps(meta).encode()).decode()


def decode_delegation(value: str) -> RequestParamsMeta:
    from typing import cast

    try:
        if len(value) > 45000:
            raise ValueError
        result = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(result, dict):
            raise ValueError
        return cast(RequestParamsMeta, result)
    except ValueError, TypeError:
        raise AppError(ErrorCode.FORBIDDEN, "Invalid service delegation") from None


class NetworkMCPClient:
    """Each bounded request initializes a fresh SDK session; independent restart needs no replay."""

    def __init__(self, config: HTTPMCPConfig, credentials: ServiceCredentials) -> None:
        self.config, self.credentials = config, credentials
        self.handshakes: list[dict[str, Any]] = []

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Client]:
        async with (
            httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + self.credentials.bearer.get_secret_value()},
                timeout=30,
                trust_env=False,
                follow_redirects=False,
            ) as http,
            Client(
                streamable_http_client(self.config.url, http_client=http),
                cache=None,
                mode="legacy",
            ) as client,
        ):
            if (
                client.session.initialize_result is None
                or client.session.protocol_version != "2025-11-25"
            ):
                raise AppError(
                    ErrorCode.MODEL_CAPABILITY_UNAVAILABLE,
                    "MCP initialize version differs",
                )
            self.handshakes.append(
                {"initialized": True, "protocol_version": client.session.protocol_version}
            )
            del self.handshakes[:-64]
            yield client

    async def list_tools(self) -> Any:
        async with self.session() as client:
            return await client.list_tools()

    async def read_resource(self, uri: str, *, meta: RequestParamsMeta, cache_mode: str) -> Any:
        async with self.session() as client:
            return await client.read_resource(uri, meta=meta, cache_mode="bypass")

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: RequestParamsMeta
    ) -> Any:
        async with self.session() as client:
            return await client.call_tool(name, arguments, meta=meta)


def delegation_nonce() -> str:
    return uuid4().hex
