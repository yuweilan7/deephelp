"""Immutable Prompt export and revision-bound annotation evidence; no auto-review."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider

from deephelp_app.adapters.asset_integrity import file_digest
from deephelp_app.adapters.native_observation import configuration
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.intent_prompt import (
    PromptArtifact,
    default_system,
    fallback_contract,
    read_prompt,
)
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.feedback import FeedbackCandidate
from deephelp_app.domain.models.intents import IntentCode
from deephelp_tools.evaluation.flywheel_store import FeedbackStore
from deephelp_tools.evaluation.langfuse_mapping import (
    ImportIds,
    Platform,
    _ids,
    import_lock,
    json_value,
)

SCORE_NAME = "m29_intent_label"


async def export_prompt(
    platform: Platform, name: str, version: int, output: Path
) -> PromptArtifact:
    if version < 1:
        raise ConfigurationError("Export an exact immutable Prompt version")
    row = await platform.request(
        "GET", "v2/prompts/" + quote(name, safe=""), params={"version": version}
    )
    if not row or row["name"] != name or row["version"] != version or row["type"] != "text":
        raise ConfigurationError("Immutable text Prompt version unavailable")
    artifact = PromptArtifact(
        name=name,
        version=version,
        text=row["prompt"],
        contract=row["config"],
        request_digest=digest([row["prompt"], row["config"]]),
    )
    # Ownership covers the read/write decision; concurrent versions cannot replace a file.
    with import_lock(output):
        if output.exists():  # noqa: ASYNC240 -- bounded atomic artifact ownership
            if read_prompt(output) != artifact:
                raise ConfigurationError("Local Prompt artifact is immutable")
        else:
            # Validate before writing so an incompatible template never becomes a candidate.
            if (
                artifact.contract != fallback_contract()
                or "{{" in artifact.text
                or "}}" in artifact.text
            ):
                raise ConfigurationError("Prompt format/schema contract is incompatible")
            atomic_json(output, artifact.model_dump(mode="json"))
        return read_prompt(output)


def candidate_binding(candidate: FeedbackCandidate) -> dict[str, Any]:
    fingerprint = digest(candidate.model_dump(mode="json"))
    ids = digest(["m29-feedback", candidate.candidate_id, candidate.revision, fingerprint])
    return dict(
        trace_id=ids[:32],
        observation_id=ids[32:48],
        metadata=dict(
            candidate_id=candidate.candidate_id,
            revision=candidate.revision,
            candidate_digest=fingerprint,
            source="m18-feedback-candidate",
        ),
    )


async def annotations(platform: Platform, candidate: FeedbackCandidate) -> dict[str, Any]:
    binding = candidate_binding(candidate)
    result = await platform.request(
        "GET",
        "v2/observations",
        params={"traceId": binding["trace_id"], "limit": 100, "fields": "core,basic,io,metadata"},
    )
    rows = [r for r in result["data"] if r["id"] == binding["observation_id"]]
    if not rows:
        return dict(status="NOT_EXPORTED", scores=[], binding=binding, url=None)
    if len(rows) != 1:
        raise ConfigurationError("Candidate export is ambiguous")
    row = rows[0]
    metadata = json_value(row["metadata"])
    if (
        row["traceId"] != binding["trace_id"]
        or not row.get("endTime")
        or any(metadata.get(k) != v for k, v in binding["metadata"].items())
        or json_value(row["input"]) != {"text": candidate.text}
        or json_value(row["output"])
        != {"candidate_observation_json": json.dumps(candidate.observation, ensure_ascii=False)}
    ):
        raise ConfigurationError("Platform observation does not bind this exact candidate revision")
    scores = await platform.request(
        "GET",
        "v3/scores",
        params={
            "traceId": binding["trace_id"],
            "observationId": binding["observation_id"],
            "fields": "subject,details,annotation",
            "limit": 100,
        },
    )
    if scores.get("meta", {}).get("cursor"):
        raise ConfigurationError("Annotation page exceeds the bounded review scope")
    # V3 has a typed value and discriminated subject; preserve the wire row in
    # the evidence digest and expose explicit label/scope to the original review.
    eligible = [
        dict(
            s,
            traceId=s["subject"]["traceId"],
            observationId=s["subject"]["id"],
            stringValue=s["value"],
        )
        for s in scores["data"]
        if (
            s.get("source") == "ANNOTATION"
            and s.get("authorUserId")
            and s.get("subject", {}).get("kind") == "observation"
            and s["subject"].get("traceId") == binding["trace_id"]
            and s["subject"].get("id") == binding["observation_id"]
            and s.get("name") == SCORE_NAME
            and s.get("dataType") == "CATEGORICAL"
            and s.get("value") in {c.value for c in IntentCode if c.actionable}
        )
    ]
    return dict(
        status="EXPORTED",
        scores=eligible,
        binding=binding,
        url=platform.config["host"].rstrip("/")
        + "/project/"
        + row["projectId"]
        + "/traces/"
        + binding["trace_id"],
    )


async def export_candidate(platform: Platform, candidate: FeedbackCandidate) -> dict[str, Any]:
    # This function is called only by the isolated CLI writer, never by the app process.
    existing = await annotations(platform, candidate)
    if existing["status"] == "EXPORTED":
        return existing
    binding = candidate_binding(candidate)
    sdk = Langfuse(
        public_key=platform.config["public_key"],
        secret_key=platform.config["secret_key"],
        base_url=platform.config["host"],
        timeout=3,
        environment="m29-feedback",
        tracer_provider=TracerProvider(id_generator=ImportIds()),
    )
    try:
        token = _ids.set((binding["trace_id"], binding["observation_id"]))
        try:
            with sdk.start_as_current_observation(
                name="m29-feedback-candidate",
                input={"text": candidate.text},
                output={
                    "candidate_observation_json": json.dumps(
                        candidate.observation, ensure_ascii=False
                    )
                },
                metadata=binding["metadata"],
                version=str(candidate.revision),
            ):
                pass
        finally:
            _ids.reset(token)
        await asyncio.to_thread(sdk.flush)
        async with asyncio.timeout(60):
            while True:
                current = await annotations(platform, candidate)
                if current["status"] == "EXPORTED":
                    return current
                await asyncio.sleep(1)  # noqa: ASYNC110 -- actual platform readback
    finally:
        await asyncio.shield(asyncio.to_thread(sdk.shutdown))


def require_annotation(
    candidate: FeedbackCandidate,
    revision: int,
    evidence: dict[str, Any],
    score_id: str,
    label: IntentCode | None,
) -> dict[str, Any]:
    if candidate.revision != revision or evidence["binding"] != candidate_binding(candidate):
        raise ConfigurationError("Candidate revision changed before platform review")
    scores: list[dict[str, Any]] = [s for s in evidence["scores"] if s["id"] == score_id]
    binding = candidate_binding(candidate)
    if len(scores) != 1 or not (
        evidence.get("status") == "EXPORTED"
        and scores[0].get("source") == "ANNOTATION"
        and scores[0].get("authorUserId")
        and scores[0].get("traceId") == binding["trace_id"]
        and scores[0].get("observationId") == binding["observation_id"]
        and scores[0].get("name") == SCORE_NAME
        and scores[0].get("dataType") == "CATEGORICAL"
        and scores[0].get("stringValue") in {c.value for c in IntentCode if c.actionable}
        and (label is None or scores[0]["stringValue"] == label.value)
    ):
        raise ConfigurationError(
            "Explicit annotation/independent label does not match this revision"
        )
    return scores[0]


async def run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client, asyncio.timeout(120):
        platform = Platform(client, configuration(str(args.config)))
        if args.command == "score-config":
            configs = await platform.request("GET", "score-configs", params={"limit": 100})
            if configs.get("meta", {}).get("totalPages", 1) > 1:
                raise ConfigurationError("Score configuration page exceeds the bounded scope")
            matching = [row for row in configs["data"] if row["name"] == SCORE_NAME]
            categories = [
                dict(label=code.value, value=index)
                for index, code in enumerate(IntentCode)
                if code.actionable
            ]
            if len(matching) > 1:
                raise ConfigurationError("Platform annotation configuration is ambiguous")
            result = (
                matching[0]
                if matching
                else await platform.request(
                    "POST",
                    "score-configs",
                    idempotent=False,
                    json=dict(
                        name=SCORE_NAME,
                        dataType="CATEGORICAL",
                        categories=categories,
                        description=(
                            "Synthetic label evidence; explicit M18 revision review still required"
                        ),
                    ),
                )
            )
            if result["dataType"] != "CATEGORICAL" or result["categories"] != categories:
                raise ConfigurationError("Platform annotation categories differ")
            print(json.dumps(dict(name=result["name"], id=result["id"])))
        elif args.command == "create":
            result = await platform.request(
                "POST",
                "v2/prompts",
                idempotent=False,
                json=dict(
                    name=args.name,
                    type="text",
                    prompt=default_system(),
                    config=fallback_contract(),
                    labels=["m29-candidate"],
                    commitMessage="现有主意图兜底文本等价导入/版本演练",
                ),
            )
            print(json.dumps(dict(name=result["name"], version=result["version"])))
        elif args.command == "export":
            if args.version is None or args.output is None:
                raise ConfigurationError("Export requires exact version and artifact path")
            prompt = await export_prompt(platform, args.name, args.version, args.output)
            print(
                json.dumps(
                    dict(
                        name=prompt.name,
                        version=prompt.version,
                        sha256=file_digest(args.output),
                        request_digest=prompt.request_digest,
                    )
                )
            )
        else:
            if not args.candidate_id or args.revision is None or args.output is None:
                raise ConfigurationError("Candidate export requires ID/revision/output")
            store = await FeedbackStore.open(Path.cwd())
            try:
                candidate = await store.get(args.candidate_id)
                if candidate.revision != args.revision:
                    raise ConfigurationError("Candidate revision changed before export")
                result = await export_candidate(platform, candidate)
                atomic_json(args.output, result)
                print(json.dumps(dict(status=result["status"], url=result["url"], model_calls=0)))
            finally:
                await store.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "export", "candidate", "score-config"))
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", default="deephelp-intent-fallback")
    parser.add_argument("--version", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-id")
    parser.add_argument("--revision", type=int)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
