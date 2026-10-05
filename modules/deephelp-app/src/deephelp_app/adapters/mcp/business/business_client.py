"""应用的可信维护代理，转发到独立Business仓储事务而不复制实现。

身份由原入口提供，参数和响应按唯一DTO验证；连接/超时/无效内容分别报告。
record_path编码对象路径，业务记录归属和CAS仍由服务端复验。
"""

import hashlib
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.adapters.mcp.shared.network_mcp import (
    HTTPMCPConfig,
    delegation_nonce,
    encode_delegation,
)
from deephelp_app.domain.checks import require_owner
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.business import BusinessRecord
from deephelp_app.domain.models.common import ErrorCode, ErrorDetail, VerifiedIdentity


class BusinessClient:
    def __init__(self, config: HTTPMCPConfig) -> None:
        self.config, self.credentials = config, config.credentials()

    async def request(
        self,
        identity: VerifiedIdentity,
        method: str,
        path: str,
        body: Any = None,
    ) -> Any:
        target = "/records/" + path
        metadata = self.credentials.protect(
            "business",
            {
                "action": "maintain",
                "nonce": delegation_nonce(),
                "identity": identity.model_dump(mode="json"),
                "method": method,
                "target": target,
                "body_hash": hashlib.sha256(canonical(body)).hexdigest(),
            },
        )
        try:
            async with httpx.AsyncClient(
                timeout=15, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.request(
                    method,
                    self.config.url.removesuffix("/mcp") + target,
                    headers={
                        "Authorization": "Bearer " + self.credentials.bearer.get_secret_value(),
                        "X-Deephelp-Delegation": encode_delegation(metadata),
                    },
                    **({"json": body} if body is not None else {}),
                )
            if len(response.content) > 1048576:
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Business response too large")
            data = response.json()
            if response.status_code != 200:
                try:
                    error = ErrorDetail.model_validate(data["error"])
                except KeyError, TypeError, ValidationError:
                    raise AppError(
                        ErrorCode.UPSTREAM_UNAVAILABLE, "Business service rejected request"
                    ) from None
                raise AppError(
                    error.code, "Business maintenance request rejected", retryable=error.retryable
                )
            try:
                if path.startswith("evidence/"):
                    for record in data["snapshot"]["records"]:
                        require_owner(identity, BusinessRecord.model_validate(record).identity)
                elif "activity_ids" in data and "version" in data:
                    if type(data["version"]) is not int or data["version"] <= 0:
                        raise ValueError
                elif isinstance(data, dict) and "items" in data:
                    for record in data["items"]:
                        require_owner(identity, BusinessRecord.model_validate(record).identity)
                else:
                    record = BusinessRecord.model_validate(data)
                    require_owner(identity, record.identity)
            except ValidationError, KeyError, TypeError, ValueError:
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID, "Invalid business record response"
                ) from None
            return data
        except httpx.TimeoutException:
            raise AppError(ErrorCode.TIMEOUT, "Business service timed out") from None
        except httpx.TransportError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Business service unavailable") from None
        except ValueError:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Invalid business service response"
            ) from None


def record_path(kind: str, record_id: str) -> str:
    return kind + "/" + quote(record_id, safe="")
