"""显式模型端点、能力、价格和向量签名配置，供装配创建网关。

load只读取指定配置；create_gateway才解析所需环境凭据并组合已打开的HTTP客户端。
配置不自动发现模型或按额度切换，Chat/强判/Embedding/Rerank保留各自绑定。
"""

import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.execution import AsyncCalls
from deephelp_app.domain.models.common import DecimalValue
from deephelp_app.domain.models.model_io import EmbeddingSignature

if TYPE_CHECKING:
    from deephelp_app.adapters.model_gateway.gateway import QianwenGateway


class EndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1)
    base_url_env: str = "DEEPHELP_MODEL_BASE_URL"
    api_key_env: str = "DEEPHELP_MODEL_API_KEY"
    input_cny_per_million: DecimalValue
    output_cny_per_million: DecimalValue = Decimal("0")


class RerankEndpointConfig(EndpointConfig):
    model: Literal["qwen3.7-text-rerank"] = "qwen3.7-text-rerank"
    # This env field contains the complete native endpoint, never a Chat base URL.
    base_url_env: str = "DEEPHELP_RERANK_URL"

    @model_validator(mode="after")
    def input_only_price(self) -> RerankEndpointConfig:
        if self.output_cny_per_million != 0:
            raise ValueError("Text rerank is metered as input only")
        return self


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str = "qianwen"
    chat: EndpointConfig
    embedding: EndpointConfig
    rerank: RerankEndpointConfig | None = None
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
            config = cls.model_validate_json(resolve_artifact(path).read_text(encoding="utf-8-sig"))
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
    from deephelp_app.adapters.model_gateway.gateway import QianwenGateway

    source = os.environ if env is None else env
    chat = endpoint_credentials(config.chat, source)
    embedding = endpoint_credentials(config.embedding, source)
    rerank = endpoint_credentials(config.rerank, source) if config.rerank else None
    if rerank and not urlsplit(rerank[0]).path.endswith(
        "/api/v1/services/rerank/text-rerank/text-rerank"
    ):
        raise ConfigurationError("Rerank requires an explicit native text sorting endpoint")
    return QianwenGateway(client, calls, config, chat, embedding, rerank)
