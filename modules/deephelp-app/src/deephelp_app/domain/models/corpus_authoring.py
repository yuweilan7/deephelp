"""语料作者的值契约：记录沿用CorpusRecord，审核信息不是业务事实或工具许可。

目录版本由工作区固定，revision由PG负责CAS；客户端不能指定owner或审核者。
"""

from typing import Annotated, Literal

from pydantic import Field

from .common import DTO, Identifier
from .retrieval import CorpusRecord

Revision = Annotated[int, Field(strict=True, ge=0)]
Source = Annotated[str, Field(strict=True, min_length=1, max_length=512, pattern=r"\S")]


class CorpusEdit(DTO):
    revision: Revision
    record: CorpusRecord
    source: Source


class CorpusReview(DTO):
    revision: Revision
    doc_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=1000)
    approve: bool
    reason: Source
    confirmed: Literal[True]


class CorpusImport(DTO):
    revision: Revision
    preview_id: Identifier
    confirmed: Literal[True]


class CorpusUpload(DTO):
    revision: Revision
    format: Literal["jsonl", "csv", "xlsx"]
    data_base64: str = Field(max_length=2_800_000)
    columns: dict[str, str] = Field(default_factory=dict, max_length=16)
