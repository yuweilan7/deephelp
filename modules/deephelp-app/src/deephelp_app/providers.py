"""Explicit provider configuration; this file never discovers credentials or makes I/O."""

import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deephelp_app.domain.models import DecimalValue, EmbeddingSignature
from deephelp_app.errors import ConfigurationError
from deephelp_app.execution import AsyncCalls

if TYPE_CHECKING:
    from deephelp_app.gateway import QianwenGateway


class EndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1)
    base_url_env: str = "DEEPHELP_MODEL_BASE_URL"
    api_key_env: str = "DEEPHELP_MODEL_API_KEY"
    input_cny_per_million: DecimalValue
    output_cny_per_million: DecimalValue = Decimal("0")


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "qianwen"
    chat: EndpointConfig
    embedding: EndpointConfig
    embedding_revision: str | None = None
    dimension: int = Field(default=1024, ge=1, le=4096)
    normalization: str = "l2"
    allow_zero_embedding_indices: bool = False
    strict_schema: bool = True
    tool_calling: bool = True
    enable_thinking: bool = False
    thinking_budget: int = Field(default=8192, ge=1)
    batch_size: int = Field(default=20, ge=1, le=20)
    max_texts: int = Field(default=128, ge=1)
    max_request_bytes: int = Field(default=1048576, ge=1024)
    max_response_bytes: int = Field(default=16777216, ge=1024)
    cache_entries: int = Field(default=64, ge=0, le=256)
    cache_bytes: int = Field(default=4194304, ge=0, le=8388608)

    def signature(self) -> EmbeddingSignature:
        return EmbeddingSignature.model_validate(
            {
                "provider": self.provider,
                "model": self.embedding.model,
                "revision": self.embedding_revision,
                "dimension": self.dimension,
                "normalization": self.normalization,
            }
        )

    @classmethod
    def load(cls, path: Path) -> ProviderConfig:
        try:
            config = cls.model_validate_json(path.read_text(encoding="utf-8-sig"))
            config.signature()
            if config.allow_zero_embedding_indices and (
                config.provider != "qianwen"
                or config.embedding.model != "qwen3.7-text-embedding-flash"
            ):
                raise ValueError("Position fallback has only been verified for the selected model")
            return config
        except OSError, ValueError, ValidationError:
            raise ConfigurationError("Invalid provider configuration file") from None


def endpoint_credentials(
    endpoint: EndpointConfig,
    env: Mapping[str, str],
) -> tuple[str, str]:
    base = env.get(endpoint.base_url_env, "").rstrip("/")
    key = env.get(endpoint.api_key_env, "").strip()
    url = urlsplit(base)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not key
    ):
        raise ConfigurationError("Provider requires HTTPS URL and nonempty credential fields")
    return base, key


def create_gateway(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    calls: AsyncCalls,
    *,
    env: Mapping[str, str] | None = None,
) -> QianwenGateway:
    from deephelp_app.gateway import QianwenGateway

    source = os.environ if env is None else env
    chat = endpoint_credentials(config.chat, source)
    embedding = endpoint_credentials(config.embedding, source)
    return QianwenGateway(client, calls, config, chat, embedding)
