"""Static fallback request contract and immutable, locally published prompt artifact."""

from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.common import DTO, Identifier
from deephelp_app.domain.models.release import ReleaseManifest

ROLE = "langfuse:intent-fallback"


class PromptArtifact(DTO):
    format: Literal["m29-prompt-v1"] = "m29-prompt-v1"
    role: Literal["intent-fallback"] = "intent-fallback"
    name: Identifier
    version: int = Field(ge=1, strict=True)
    text: str = Field(min_length=1, max_length=16384)
    contract: dict[str, Any]
    request_digest: Identifier


def default_system() -> str:
    return (
        files("deephelp_app.resources").joinpath("intent_fallback.txt").read_text(encoding="utf-8")
    )


def fallback_contract() -> dict[str, Any]:
    from deephelp_app.application.intent import INTENT_SCHEMA

    return dict(
        role="system",
        template_engine="none",
        variables=[],
        input_keys=["text", "confirmed_event_context", "retrievals"],
        serialization="json.dumps ensure_ascii=False; complete text and confirmed context",
        response_format="json_schema",
        output_schema=INTENT_SCHEMA,
        max_output_tokens=2048,
        repair_once=False,
        tools=[],
        model_binding="ReleaseManifest.judge_providers_digest",
    )


def read_prompt(path: Path) -> PromptArtifact:
    from deephelp_app.application.artifact_paths import resolve_artifact

    resolved = resolve_artifact(path)
    if resolved.stat().st_size > 100_000:
        raise ConfigurationError("Prompt artifact exceeds the static contract limit")
    prompt = PromptArtifact.model_validate_json(resolved.read_text(encoding="utf-8"))
    if (
        prompt.contract != fallback_contract()
        or "{{" in prompt.text
        or "}}" in prompt.text
        or prompt.request_digest != digest([prompt.text, prompt.contract])
    ):
        raise ConfigurationError("Prompt template/schema/request contract changed")
    return prompt


def published_prompt(manifest: ReleaseManifest) -> tuple[Path, PromptArtifact] | None:
    from deephelp_app.adapters.asset_integrity import file_digest

    extras = {key for key in manifest.prompts if key.startswith("langfuse:")}
    if not extras:
        return None
    if extras != {ROLE}:
        raise ConfigurationError("Unsupported published Prompt role")
    fingerprint = manifest.prompts[ROLE]
    paths = [Path(path) for path, value in manifest.files.items() if value == fingerprint]
    if len(paths) != 1 or file_digest(paths[0]) != fingerprint:
        raise ConfigurationError("Published Prompt has no unique immutable local artifact")
    return paths[0], read_prompt(paths[0])
