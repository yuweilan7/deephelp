"""Manual business maintenance through the application's single trusted identity entry point."""

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from deephelp_app.adapters.business_client import BusinessClient, record_path
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.business import (
    BusinessInput,
    BusinessRecord,
    BusinessRelations,
    BusinessUpdate,
)
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity

Kind = Literal["orders", "coupons", "activities"]


def business_router(
    client: BusinessClient, auth: Callable[[Request], VerifiedIdentity]
) -> APIRouter:
    router = APIRouter(prefix="/business", tags=["Manual business records"])

    @router.get("/evidence/{evidence_id}")
    async def evidence(evidence_id: str, request: Request) -> Any:
        return await client.request(auth(request), "GET", record_path("evidence", evidence_id))

    @router.get("/{kind}")
    async def listing(
        kind: Kind,
        request: Request,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(20, ge=1, le=100),
    ) -> Any:
        return await client.request(auth(request), "GET", f"{kind}?offset={offset}&limit={limit}")

    @router.post("/{kind}", response_model=BusinessRecord)
    async def creating(kind: Kind, body: BusinessInput, request: Request) -> Any:
        return await client.request(auth(request), "POST", kind, body.model_dump(mode="json"))

    @router.get("/orders/{order_id}/activities")
    async def get_relations(order_id: str, request: Request) -> Any:
        return await client.request(
            auth(request), "GET", record_path("orders", order_id) + "/activities"
        )

    @router.put("/orders/{order_id}/activities")
    async def relations(order_id: str, body: BusinessRelations, request: Request) -> Any:
        return await client.request(
            auth(request),
            "PUT",
            record_path("orders", order_id) + "/activities",
            body.model_dump(mode="json"),
        )

    @router.get("/{kind}/{record_id}", response_model=BusinessRecord)
    async def detail(kind: Kind, record_id: str, request: Request) -> Any:
        return await client.request(auth(request), "GET", record_path(kind, record_id))

    @router.put("/{kind}/{record_id}", response_model=BusinessRecord)
    async def updating(kind: Kind, record_id: str, body: BusinessUpdate, request: Request) -> Any:
        id_field = {"orders": "order_id", "coupons": "coupon_id", "activities": "activity_id"}[kind]
        if getattr(body.data, id_field, None) != record_id:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Path and business ID differ")
        return await client.request(
            auth(request), "PUT", record_path(kind, record_id), body.model_dump(mode="json")
        )

    return router
