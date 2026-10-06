"""原记忆工作台的明确保留/范围/候选入口；BrowserAuth继续负责身份和CSRF。

读取及确定性保留模型0次。服务来自父lifespan就绪的原Conversation，router不建第二池。
"""

from fastapi import APIRouter, Request
from pydantic import Field

from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import DTO, Count, ErrorCode, Identifier
from deephelp_app.domain.models.long_memory import MemoryChange


class PreserveMemory(DTO):
    source_session: Identifier
    source_question: Identifier


class ConfigureMemory(DTO):
    expected_revision: Count
    enabled: bool
    source_sessions: tuple[Identifier, ...] = Field(default=(), max_length=8)


def long_memory_router(auth: BrowserAuth) -> APIRouter:
    router = APIRouter(prefix="/learning/long-memory")

    def service(request: Request) -> LongMemoryService:
        memory = request.app.state.resources.conversation.long_memory
        if not isinstance(memory, LongMemoryService):
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Long memory is not configured", 501)
        return memory

    @router.post("/preserve")
    async def preserve(body: PreserveMemory, request: Request) -> object:
        owner = auth(request)
        return await service(request).preserve(owner, body.source_session, body.source_question)

    @router.get("/{session}")
    async def candidates(session: Identifier, request: Request) -> object:
        owner = auth(request)
        return await service(request).candidates(owner, session)

    @router.get("/records/{memory_id}")
    async def detail(memory_id: Identifier, request: Request) -> object:
        return await service(request).detail(auth(request), memory_id)

    @router.put("/records/{memory_id}")
    async def change(memory_id: Identifier, body: MemoryChange, request: Request) -> object:
        return await service(request).change(auth(request), memory_id, body)

    @router.put("/{session}/scope")
    async def configure(session: Identifier, body: ConfigureMemory, request: Request) -> object:
        owner = auth(request)
        return await service(request).configure(
            owner,
            session,
            body.expected_revision,
            body.enabled,
            body.source_sessions,
        )

    return router
