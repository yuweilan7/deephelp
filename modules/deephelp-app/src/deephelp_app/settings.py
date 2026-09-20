import os
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from deephelp_app.errors import ConfigurationError


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    mode: Literal["dev", "test", "live"] = "dev"
    request_timeout: float = Field(default=5.0, gt=0, le=120)
    child_timeout: float = Field(default=1.0, gt=0, le=120)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_limit: int = Field(default=1, ge=0, le=9)
    http_connections: int = Field(default=10, ge=1, le=100)
    llm_concurrency: int = Field(default=2, ge=1, le=2)
    trace_path: str = "logs/m01-trace.jsonl"
    model_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    model_base_url: str | None = Field(default=None, exclude=True, repr=False)
    enable_live: bool = False
    live_max_calls: int = Field(default=0, ge=0, le=10)
    live_max_tokens: int = Field(default=0, ge=0)
    live_max_cost: Decimal = Field(default=Decimal("0"), ge=0)
    live_model: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """No automatic dotenv discovery: the caller explicitly supplies the environment."""
        source = os.environ if env is None else env
        values = {
            name: source[f"DEEPHELP_{name.upper()}"]
            for name in cls.model_fields
            if f"DEEPHELP_{name.upper()}" in source
        }
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            fields = sorted({str(error["loc"][0]) for error in exc.errors()})
            raise ConfigurationError("Invalid settings fields: " + ", ".join(fields)) from None

    def validate_live(self) -> None:
        if self.mode != "live":
            return
        if not self.enable_live:
            raise ConfigurationError("Live requires DEEPHELP_ENABLE_LIVE=true")
        if self.model_api_key is None or not self.model_api_key.get_secret_value().strip():
            raise ConfigurationError("Live requires DEEPHELP_MODEL_API_KEY")
        if not self.model_base_url or not self.model_base_url.startswith("https://"):
            raise ConfigurationError("Live requires an HTTPS DEEPHELP_MODEL_BASE_URL")
        if not self.live_model:
            raise ConfigurationError("Live requires DEEPHELP_LIVE_MODEL")
        if self.live_max_calls <= 0 or self.live_max_tokens <= 0 or self.live_max_cost <= 0:
            raise ConfigurationError("Live requires positive call, token and cost budgets")
