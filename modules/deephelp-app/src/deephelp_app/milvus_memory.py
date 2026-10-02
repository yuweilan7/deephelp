"""Versioned M10 event index; old rows cannot overwrite newer facts or authorize routing."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pymilvus import DataType, MilvusException  # type: ignore[import-untyped]

from deephelp_app.cases import summary
from deephelp_app.domain.models import (
    CaseSummary,
    EmbeddingSignature,
    ErrorCode,
    Question,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.memory import scope_key
from deephelp_app.ports import EmbeddingPort


class MilvusEventIndex:
    def __init__(
        self,
        client: Any,
        embedding: EmbeddingPort,
        signature: EmbeddingSignature,
        collection: str | None = None,
    ) -> None:
        self.client, self.embedding, self.signature = client, embedding, signature
        self.collection = collection or "dh_m10_events_" + signature.fingerprint[-16:]
        if (
            not self.collection.startswith("dh_m10_events_")
            or not self.collection.replace("_", "").isalnum()
        ):
            raise ConfigurationError("M10 owns only dh_m10_events_ collections")

    async def rpc[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            async with asyncio.timeout(15):
                return await operation()
        except MilvusException as exc:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Memory Milvus request failed",
                provider_code=str(exc.code),
            ) from None

    async def initialize(self) -> None:
        description = json.dumps(
            {"format": "m10-event-v1", "signature": self.signature.model_dump(mode="json")},
            sort_keys=True,
        )
        if not await self.rpc(
            lambda: self.client.has_collection(self.collection, timeout=None, retry_times=0)
        ):
            schema = self.client.create_schema(
                auto_id=False, enable_dynamic_field=False, description=description
            )
            schema.add_field(
                field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=64
            )
            schema.add_field(field_name="scope", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="question_id", datatype=DataType.VARCHAR, max_length=128)
            schema.add_field(field_name="version", datatype=DataType.INT64)
            schema.add_field(field_name="status", datatype=DataType.VARCHAR, max_length=32)
            schema.add_field(field_name="summary", datatype=DataType.VARCHAR, max_length=8192)
            schema.add_field(field_name="message_ids", datatype=DataType.JSON)
            schema.add_field(
                field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self.signature.dimension
            )
            indexes = self.client.prepare_index_params()
            indexes.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")
            await self.rpc(
                lambda: self.client.create_collection(
                    self.collection,
                    schema=schema,
                    index_params=indexes,
                    consistency_level="Strong",
                    timeout=None,
                    retry_times=0,
                )
            )
        details = await self.rpc(
            lambda: self.client.describe_collection(self.collection, timeout=None, retry_times=0)
        )
        if details["description"] != description:
            raise ConfigurationError(
                "Event collection signature/schema differs; reindex into a new collection"
            )
        fields = {field["name"]: field for field in details["fields"]}
        if (
            set(fields)
            != {
                "id",
                "scope",
                "question_id",
                "version",
                "status",
                "summary",
                "message_ids",
                "vector",
            }
            or details["auto_id"]
            or details["enable_dynamic_field"]
            or int(fields["vector"]["params"]["dim"]) != self.signature.dimension
        ):
            raise ConfigurationError("Event collection physical schema differs")
        await self.rpc(
            lambda: self.client.load_collection(self.collection, timeout=None, retry_times=0)
        )

    async def put(self, question: Question, budget: ExecutionBudget) -> None:
        record = summary(question)
        result = await self.embedding.embed([record.text], budget)
        if result.signature != self.signature or len(result.vectors) != 1:
            raise ConfigurationError("Memory embedding signature differs")
        row = {
            "id": hashlib.sha256(f"{question.question_id}:{question.version}".encode()).hexdigest(),
            "scope": scope_key(question.identity, question.session_id),
            "question_id": question.question_id,
            "version": question.version,
            "status": question.status.value,
            "summary": record.text,
            "message_ids": list(record.message_ids),
            "vector": result.vectors[0],
        }
        await self.rpc(
            lambda: self.client.upsert(self.collection, data=[row], timeout=None, retry_times=0)
        )

    async def search(
        self, identity: VerifiedIdentity, session: str, query: str, budget: ExecutionBudget
    ) -> list[CaseSummary]:
        result = await self.embedding.embed([query], budget)
        if result.signature != self.signature:
            raise ConfigurationError("Memory query signature differs")
        hits = await self.rpc(
            lambda: self.client.search(
                self.collection,
                data=[result.vectors[0]],
                filter=f'scope == "{scope_key(identity, session)}"',
                limit=12,
                output_fields=["question_id", "version", "status", "summary", "message_ids"],
                consistency_level="Strong",
                timeout=None,
                retry_times=0,
            )
        )
        return [
            CaseSummary(
                question_id=h["entity"]["question_id"],
                version=h["entity"]["version"],
                status=h["entity"]["status"],
                text=h["entity"]["summary"],
                message_ids=tuple(h["entity"]["message_ids"]),
            )
            for h in hits[0]
        ]
