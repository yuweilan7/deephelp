"""Async Milvus FP32/FLAT adapter. Credentials and operational targets stay local."""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from pymilvus import (  # type: ignore[import-untyped]
    AsyncMilvusClient,
    DataType,
    MilvusClient,
    MilvusException,
)

from deephelp_app.dense import (
    Capacity,
    DenseStore,
    atomic_json,
    existing_json,
    load_json,
    validate_vector,
    verify_manifest_rows,
)
from deephelp_app.domain.models import DenseHit, DenseScope, ErrorCode, IntentCode
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget

FIELDS = {
    "doc_id": (DataType.VARCHAR, 128),
    "content": (DataType.VARCHAR, 8192),
    "intent_code": (DataType.VARCHAR, 128),
    "namespace": (DataType.VARCHAR, 64),
    "dataset_version": (DataType.VARCHAR, 128),
    "model_signature": (DataType.VARCHAR, 80),
    "split": (DataType.VARCHAR, 16),
    "content_hash": (DataType.VARCHAR, 64),
}
OUTPUT = list(FIELDS) + ["metadata", "vector"]


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def local_connection(root: Path) -> dict[str, str]:
    values = read_env(root / "infra/config/connections.env")
    secret = Path(
        os.environ.get("DEEPHELP_SECRET_FILE", str(Path.home() / ".deephelp/.env.secret"))
    )
    values.update(read_env(secret))
    return values


def create_client(root: Path) -> AsyncMilvusClient:
    config = local_connection(root)
    # SDK exception logging can include request payloads; diagnostics use AppError codes instead.
    logging.getLogger("pymilvus").setLevel(logging.CRITICAL)
    return AsyncMilvusClient(
        uri=config["MILVUS_URI"],
        user=config["MILVUS_USER"],
        password=config["MILVUS_PASSWORD"],
        db_name=config["MILVUS_DATABASE"],
        timeout=15,
    )


class SSHCapacity:
    """Read-only checks against the existing P00 host; no deployment or restart command."""

    def __init__(self, root: Path) -> None:
        self.root, self.config = root, local_connection(root)
        self.observations: list[dict[str, int]] = []

    async def __call__(self) -> Capacity:
        remote = (
            "sudo python3 -c 'import json,shutil,subprocess; "
            'd=json.loads(subprocess.check_output(["docker","inspect","deephelp-milvus"]))[0]; '
            's=json.loads(subprocess.check_output(["docker","stats","--no-stream",'
            '"--format","{{json .}}","deephelp-milvus"])) ; '
            'units={"B":1,"KiB":1024,"MiB":1048576,"GiB":1073741824}; '
            'v=s["MemUsage"].split("/")[0].strip(); '
            'u=next(k for k in ("GiB","MiB","KiB","B") if v.endswith(k)); '
            'a=int(next(l.split()[1] for l in open("/proc/meminfo") '
            'if l.startswith("MemAvailable:")))*1024; '
            'print(json.dumps({"root_free":shutil.disk_usage("/").free,'
            '"milvus_data":int(subprocess.check_output(["du","-s","-B1","/srv/deephelp-data/milvus"]).split()[0]),'
            '"memory_used":int(float(v[:-len(u)])*units[u]),"memory_limit":d["HostConfig"]["Memory"],'
            '"host_available":a,"healthy":d["State"]["Health"]["Status"]=="healthy","oom":d["State"]["OOMKilled"]}))'
            + "'"
        )
        d = self.config
        process = await asyncio.create_subprocess_exec(
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(self.root / "infra/client/known_hosts"),
            "-o",
            "ConnectTimeout=12",
            "-i",
            d["DEEPHELP_SSH_KEY_WINDOWS"],
            "-p",
            d["DEEPHELP_SSH_PORT"],
            d["DEEPHELP_SSH_USER"] + "@" + d["DEEPHELP_SSH_HOST"],
            remote,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, _ = await process.communicate()
            if process.returncode:
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Read-only capacity probe failed")
            data = json.loads(stdout)
            if not data.pop("healthy") or data.pop("oom"):
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Milvus unhealthy or OOM detected")
            capacity = Capacity(**data)
            self.observations.append(data)
            del self.observations[:-32]
            return capacity
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()


class MilvusDenseStore(DenseStore):
    def __init__(self, client: AsyncMilvusClient, budget: ExecutionBudget) -> None:
        self.client, self.budget = client, budget
        self.calls = AsyncCalls(1, 15)
        # PyMilvus 2.6.17 ignores retry_times when an SDK timeout is supplied.
        # Pass timeout=None/retry_times=0 to SDK calls; the outer async deadline
        # bounds the entire operation and propagates cancellation to the gRPC await.

    async def _rpc[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            return await self.calls.call(operation, self.budget)
        except MilvusException as exc:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "Milvus request failed", provider_code=str(exc.code)
            ) from None

    def _description(self, scope: DenseScope, corpus_digest: str) -> str:
        return json.dumps(
            {
                "format": "m05-dense-fp32-v1",
                "scope": scope.model_dump(mode="json"),
                "corpus_digest": corpus_digest,
            },
            sort_keys=True,
        )

    async def validate(self, scope: DenseScope, corpus_digest: str | None = None) -> str:
        details = await self._rpc(
            lambda: self.client.describe_collection(
                scope.collection_name, timeout=None, retry_times=0
            )
        )
        try:
            description = json.loads(details["description"])
            if description["format"] != "m05-dense-fp32-v1" or description[
                "scope"
            ] != scope.model_dump(mode="json"):
                raise ValueError
            if corpus_digest is not None and description["corpus_digest"] != corpus_digest:
                raise ValueError
            fields = {f["name"]: f for f in details["fields"]}
            if (
                details["auto_id"]
                or details["enable_dynamic_field"]
                or set(fields) != set(FIELDS) | {"metadata", "vector"}
            ):
                raise ValueError
            for key, (kind, length) in FIELDS.items():
                if (
                    int(fields[key]["type"]) != int(kind)
                    or int(fields[key]["params"]["max_length"]) != length
                ):
                    raise ValueError
            if not fields["doc_id"].get("is_primary") or int(fields["metadata"]["type"]) != int(
                DataType.JSON
            ):
                raise ValueError
            if (
                int(fields["vector"]["type"]) != int(DataType.FLOAT_VECTOR)
                or int(fields["vector"]["params"]["dim"]) != scope.signature.dimension
            ):
                raise ValueError
            return str(description["corpus_digest"])
        except ValueError, KeyError, TypeError:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Milvus schema/scope/signature mismatch"
            ) from None

    async def ensure(self, scope: DenseScope, corpus_digest: str) -> None:
        exists = await self._rpc(
            lambda: self.client.has_collection(scope.collection_name, timeout=None, retry_times=0)
        )
        if not exists:
            collections = await self._rpc(
                lambda: self.client.list_collections(timeout=None, retry_times=0)
            )
            if sum(c.startswith("m05_intent_") for c in collections) >= 4:
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED, "M05 collection limit; maintenance required"
                )
            schema = MilvusClient.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
                description=self._description(scope, corpus_digest),
            )
            for name, (kind, length) in FIELDS.items():
                schema.add_field(name, kind, max_length=length, is_primary=name == "doc_id")
            schema.add_field("metadata", DataType.JSON)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=scope.signature.dimension)
            await self._rpc(
                lambda: self.client.create_collection(
                    scope.collection_name,
                    schema=schema,
                    consistency_level="Strong",
                    num_shards=1,
                    timeout=None,
                    retry_times=0,
                )
            )
        await self.validate(scope, corpus_digest)
        indexes = await self._rpc(
            lambda: self.client.list_indexes(scope.collection_name, timeout=None, retry_times=0)
        )
        if not indexes:
            params = MilvusClient.prepare_index_params()
            params.add_index("vector", index_name="vector", index_type="FLAT", metric_type="COSINE")
            await self._rpc(
                lambda: self.client.create_index(
                    scope.collection_name, index_params=params, timeout=None, retry_times=0
                )
            )
        index = await self._rpc(
            lambda: self.client.describe_index(
                scope.collection_name, "vector", timeout=None, retry_times=0
            )
        )
        if index.get("index_type") != "FLAT" or index.get("metric_type") != "COSINE":
            raise AppError(ErrorCode.VERSION_CONFLICT, "Milvus index/metric mismatch")
        await self._rpc(
            lambda: self.client.load_collection(scope.collection_name, timeout=None, retry_times=0)
        )

    @staticmethod
    def filter(scope: DenseScope, code: IntentCode | None = None) -> str:
        fields = {
            "namespace": scope.namespace,
            "dataset_version": scope.dataset_version,
            "model_signature": scope.signature.fingerprint,
        }
        parts = [key + " == " + json.dumps(value) for key, value in fields.items()]
        parts.append('split in ["reference", "train"]')
        if code is not None:
            parts.append("intent_code == " + json.dumps(code.value))
        return " and ".join(parts)

    async def write(self, scope: DenseScope, rows: list[dict[str, object]]) -> None:
        await self.validate(scope)
        for row in rows:
            if (
                row["namespace"] != scope.namespace
                or row["dataset_version"] != scope.dataset_version
                or row["model_signature"] != scope.signature.fingerprint
                or row["split"] not in {"reference", "train"}
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Write scope mismatch")
        result = await self._rpc(
            lambda: self.client.upsert(
                scope.collection_name, data=rows, timeout=None, retry_times=0
            )
        )
        if result.get("upsert_count") != len(rows):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Milvus upsert count mismatch")

    async def read(self, scope: DenseScope, ids: list[str]) -> list[dict[str, object]]:
        await self.validate(scope)
        rows = list(
            await self._rpc(
                lambda: self.client.query(
                    scope.collection_name,
                    filter=self.filter(scope) + " and doc_id in " + json.dumps(ids),
                    output_fields=OUTPUT,
                    limit=1000,
                    consistency_level="Strong",
                    timeout=None,
                    retry_times=0,
                )
            )
        )
        for row in rows:
            validate_vector(row["vector"], scope)
        return rows

    async def finish(self, scope: DenseScope) -> None:
        await self._rpc(
            lambda: self.client.flush(scope.collection_name, timeout=None, retry_times=0)
        )
        await self._rpc(
            lambda: self.client.load_collection(scope.collection_name, timeout=None, retry_times=0)
        )

    async def search(
        self, scope: DenseScope, vector: list[float], limit: int, code: IntentCode | None = None
    ) -> list[DenseHit]:
        await self.validate(scope)
        result = await self._rpc(
            lambda: self.client.search(
                scope.collection_name,
                data=[vector],
                anns_field="vector",
                filter=self.filter(scope, code),
                limit=limit,
                output_fields=OUTPUT,
                search_params={"metric_type": "COSINE", "params": {}},
                consistency_level="Strong",
                timeout=None,
                retry_times=0,
            )
        )
        hits: list[DenseHit] = []
        for hit in result[0]:
            row = hit["entity"]
            if (
                row["namespace"] != scope.namespace
                or row["dataset_version"] != scope.dataset_version
                or row["model_signature"] != scope.signature.fingerprint
                or row["split"] not in {"train", "reference"}
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Dense result scope mismatch")
            hits.append(
                DenseHit(
                    doc_id=row["doc_id"],
                    intent_code=row["intent_code"],
                    content=row["content"],
                    raw_score=hit["distance"],
                    metadata=row["metadata"],
                    scope=scope,
                )
            )
        return hits

    async def count(self, scope: DenseScope, *, filtered: bool = True) -> int:
        rows = await self._rpc(
            lambda: self.client.query(
                scope.collection_name,
                filter=self.filter(scope) if filtered else "",
                output_fields=["count(*)"],
                consistency_level="Strong",
                timeout=None,
                retry_times=0,
            )
        )
        return int(rows[0]["count(*)"])

    async def delete_synthetic(self, scope: DenseScope, ids: list[str]) -> None:
        await self.validate(scope)
        rows = await self.read(scope, ids)
        if (
            not ids
            or len(rows) != len(ids)
            or any(
                not isinstance(row["metadata"], dict)
                or row["metadata"].get("synthetic") is not True
                for row in rows
            )
        ):
            raise ConfigurationError("Deletion requires existing synthetic IDs in this scope")
        await self._rpc(
            lambda: self.client.delete(
                scope.collection_name,
                filter=self.filter(scope) + " and doc_id in " + json.dumps(ids),
                timeout=None,
                retry_times=0,
            )
        )
        if await self.read(scope, ids):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Synthetic deletion readback failed")


async def activate(
    store: MilvusDenseStore, scope: DenseScope, manifest: Path, pointer: Path
) -> None:
    state = load_json(manifest)
    if state["status"] != "VERIFIED" or state["scope"] != scope.model_dump(mode="json"):
        raise ConfigurationError("Activation requires a verified manifest of this scope")
    await store.validate(scope, str(state["corpus_digest"]))
    ids = state["completed_ids"]
    if not isinstance(ids, list):
        raise ConfigurationError("Invalid completed IDs")
    rows = await store.read(scope, ids)
    if len(rows) != len(ids) or await store.count(scope, filtered=False) != len(rows):
        raise ConfigurationError("Activation readback/count mismatch")
    verify_manifest_rows(state, rows)
    previous = existing_json(pointer)
    atomic_json(
        pointer,
        {
            "format": "m05-pointer-v1",
            "active": scope.model_dump(mode="json"),
            "manifest": str(manifest),
            "corpus_digest": state["corpus_digest"],
            "previous": (
                {k: previous[k] for k in ("active", "manifest", "corpus_digest")}
                if previous and previous.get("active") != scope.model_dump(mode="json")
                else previous.get("previous")
                if previous
                else None
            ),
        },
    )


async def rollback(store: MilvusDenseStore, pointer: Path) -> DenseScope:
    state = load_json(pointer)
    if not state.get("previous"):
        raise ConfigurationError("No previous verified collection to roll back to")
    previous = state["previous"]
    if not isinstance(previous, dict):
        raise ConfigurationError("Invalid previous pointer")
    scope = DenseScope.model_validate(previous["active"])
    await store.validate(scope, str(previous["corpus_digest"]))
    await activate(store, scope, Path(str(previous["manifest"])), pointer)
    return scope
