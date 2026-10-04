"""Explicit synthetic platform proof; never imported by the business assembly."""

import argparse
import asyncio
import hashlib
import json
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from deephelp_app.application.dense import atomic_json


def configuration(path: Path) -> dict[str, Any]:
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8-sig"))
    for field in ("host", "s3_endpoint"):
        url = urlparse(config[field])
        if url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Platform probe requires a private loopback endpoint")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Credentials must be separate from endpoint URLs")
    if config["bucket"] != "langfuse":
        raise ValueError("Use the isolated Langfuse bucket")
    return config


def sdk(config: dict[str, Any], *, enabled: bool = True) -> Any:
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider

    return Langfuse(
        public_key=config["public_key"],
        secret_key=config["secret_key"],
        base_url=config["host"],
        timeout=3,
        flush_at=8,
        flush_interval=0.25,
        tracing_enabled=enabled,
        tracer_provider=TracerProvider(),
        environment="m27-synthetic",
    )


async def emit(config: dict[str, Any], count: int, *, enabled: bool = True) -> dict[str, Any]:
    client = sdk(config, enabled=enabled)
    traces: list[dict[str, str]] = []
    start = time.monotonic()
    try:
        for index in range(count):
            trace_id, score_id = uuid4().hex, str(uuid4())
            with client.start_as_current_observation(
                name="m27-platform-proof",
                trace_context={"trace_id": trace_id},
                input={"kind": "synthetic", "index": index},
                output={"amount": "173.45", "synthetic": True},
            ) as root:
                with client.start_as_current_observation(
                    name="m27-synthetic-generation",
                    as_type="generation",
                    model="synthetic-no-provider-call",
                    input="Synthetic platform storage check",
                    output="Synthetic amount 173.45",
                    usage_details={"input": 17, "output": 9, "total": 26},
                    metadata={"actual_model_calls": 0},
                ) as generation:
                    observation_id = generation.id
                    await asyncio.sleep(0.01)
                root_id = root.id
            if enabled:
                client.create_score(
                    trace_id=trace_id,
                    observation_id=observation_id,
                    score_id=score_id,
                    name="m27-storage-proof",
                    value=1.0,
                    data_type="NUMERIC",
                    comment="Synthetic protocol check; not model quality",
                )
            traces.append(
                {
                    "trace_id": trace_id,
                    "root_id": root_id,
                    "observation_id": observation_id,
                    "score_id": score_id,
                }
            )
        foreground_ms = (time.monotonic() - start) * 1000
        flush_start = time.monotonic()
        await asyncio.to_thread(client.flush)
        flush_ms = (time.monotonic() - flush_start) * 1000
    finally:
        await asyncio.to_thread(client.shutdown)
    return {"traces": traces, "foreground_ms": foreground_ms, "flush_ms": flush_ms}


def check_trace(body: dict[str, Any], expected: dict[str, str]) -> dict[str, Any]:
    observations = body.get("observations", [])
    root = next(item for item in observations if item["id"] == expected["root_id"])
    generation = next(item for item in observations if item["id"] == expected["observation_id"])
    if any(item["traceId"] != expected["trace_id"] for item in (root, generation)):
        raise AssertionError("Trace identity differs")
    if not root["isRootObservation"] or root["name"] != "m27-platform-proof":
        raise AssertionError("Logical root differs")
    if json.loads(root["output"]) != {"amount": "173.45", "synthetic": True}:
        raise AssertionError("Root content differs")
    if generation["parentObservationId"] != expected["root_id"]:
        raise AssertionError("Observation parent differs")
    if generation["name"] != "m27-synthetic-generation":
        raise AssertionError("Observation name differs")
    if generation["output"] != "Synthetic amount 173.45":
        raise AssertionError("Observation content differs")
    if generation["usageDetails"] != {"input": 17, "output": 9, "total": 26}:
        raise AssertionError("Synthetic usage differs")
    score = next(item for item in body["scores"] if item["id"] == expected["score_id"])
    if score["value"] != 1 or score["subject"] != {
        "kind": "observation",
        "id": expected["observation_id"],
        "traceId": expected["trace_id"],
    }:
        raise AssertionError("Score association differs")
    return {
        **expected,
        "observations": len(observations),
        "usage": generation["usageDetails"],
        "output_sha256": hashlib.sha256(generation["output"].encode()).hexdigest(),
        "score": score["value"],
        "parent_id": generation["parentObservationId"],
    }


async def verify(
    config: dict[str, Any], traces: list[dict[str, str]], wait_seconds: float
) -> list[Any]:
    results: list[Any] = []
    deadline = time.monotonic() + wait_seconds
    async with httpx.AsyncClient(
        base_url=config["host"], auth=(config["public_key"], config["secret_key"]), timeout=5
    ) as client:
        for expected in traces:
            while True:
                observations = await client.get(
                    "/api/public/v2/observations",
                    params={
                        "traceId": expected["trace_id"],
                        "fields": "core,basic,io,usage",
                        "limit": 10,
                    },
                    headers={"x-langfuse-ingestion-version": "4"},
                )
                observations.raise_for_status()
                scores = await client.get(
                    "/api/public/v3/scores",
                    params={"id": expected["score_id"], "fields": "subject", "limit": 10},
                )
                scores.raise_for_status()
                try:
                    results.append(
                        check_trace(
                            {
                                "observations": observations.json()["data"],
                                "scores": scores.json()["data"],
                            },
                            expected,
                        )
                    )
                    break
                except StopIteration:
                    pass  # Worker has not processed every observation/score yet.
                if time.monotonic() >= deadline:
                    raise TimeoutError("Langfuse content/usage/score not available within deadline")
                await asyncio.sleep(1)
    return results


def s3_client(config: dict[str, Any]) -> Any:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    return boto3.client(
        "s3",
        endpoint_url=config["s3_endpoint"],
        aws_access_key_id=config["s3_access_key"],
        aws_secret_access_key=config["s3_secret_key"],
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 0},
            connect_timeout=3,
            read_timeout=5,
        ),
    )


def s3_proof(
    config: dict[str, Any], *, retain: bool = False, create_bucket: bool = False
) -> dict[str, Any]:
    client = s3_client(config)
    bucket, key = config["bucket"], "m27-proof/" + uuid4().hex
    payload = b"M27 synthetic persistent object and metadata"
    written = False
    try:
        if create_bucket:
            client.create_bucket(Bucket=bucket)
        client.head_bucket(Bucket=bucket)
        client.put_object(Bucket=bucket, Key=key, Body=payload, Metadata={"source": "m27"})
        written = True
        item = client.get_object(Bucket=bucket, Key=key)
        with item["Body"] as body:
            assert body.read() == payload
        assert item["Metadata"]["source"] == "m27"
        assert key in [
            v["Key"] for v in client.list_objects_v2(Bucket=bucket, Prefix=key)["Contents"]
        ]
        return {
            "key": key,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "metadata": item["Metadata"],
        }
    finally:
        if written and not retain:
            client.delete_object(Bucket=bucket, Key=key)
            assert "Contents" not in client.list_objects_v2(Bucket=bucket, Prefix=key)
        client.close()


async def run(args: argparse.Namespace) -> None:
    config = configuration(args.config)
    report: dict[str, Any] = {"sdk": version("langfuse"), "model_calls": 0, "mode": args.mode}
    try:
        async with asyncio.timeout(args.timeout):
            if args.mode == "s3":
                report["object"] = await asyncio.to_thread(
                    s3_proof, config, retain=args.retain, create_bucket=args.create_bucket
                )
            elif args.mode == "verify":
                source = json.loads(args.source.read_text(encoding="utf-8"))
                report["verified"] = await verify(config, source["traces"], args.timeout - 5)
            else:
                report.update(await emit(config, args.count, enabled=args.mode != "disabled"))
                if args.mode == "roundtrip":
                    report["verified"] = await verify(config, report["traces"], args.timeout - 10)
    except Exception as error:
        report.update(status="FAIL", error_type=type(error).__name__)
        atomic_json(args.output, report)
        raise
    report["status"] = "PASS"
    report["remote_delivery"] = (
        "CONTENT_VERIFIED"
        if args.mode in {"roundtrip", "verify", "s3"}
        else "DISABLED"
        if args.mode == "disabled"
        else "UNCONFIRMED"
    )
    atomic_json(args.output, report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--mode", choices=("roundtrip", "send", "verify", "s3", "disabled"), default="roundtrip"
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--create-bucket", action="store_true")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not args.live or args.output.exists():
        parser.error("Explicit --live and a new report are required")
    if not 1 <= args.count <= 100 or not 10 <= args.timeout <= 900:
        parser.error("Bounded count 1..100 and timeout 10..900 required")
    if args.mode == "verify" and args.source is None:
        parser.error("Verify needs an existing source report")
    try:
        asyncio.run(run(args))
    except Exception as error:
        if not args.output.exists():
            atomic_json(
                args.output,
                {"status": "FAIL", "mode": args.mode, "error_type": type(error).__name__},
            )
        raise


if __name__ == "__main__":
    main()
