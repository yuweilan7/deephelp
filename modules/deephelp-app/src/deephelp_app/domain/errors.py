"""用安全静态信息表示领域/依赖错误，由API映射状态和诊断。

AppError保留错误码、可重试标记与供应商请求ID；不把凭据或原响应正文向外传播。
ConfigurationError只说明出错字段，配置值仍留在受保护环境。
"""

from deephelp_app.domain.models.common import ErrorCode

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_ARGUMENT: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.BUDGET_EXHAUSTED: 503,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    # Business closeouts must use CLARIFY/HANDOFF, not an infrastructure HTTP error.
    ErrorCode.UNKNOWN_INTENT: 200,
    ErrorCode.MISSING_SLOT: 200,
    ErrorCode.NO_SOP: 200,
    ErrorCode.MODEL_OUTPUT_INVALID: 502,
    ErrorCode.APPROVAL_INVALID: 409,
    ErrorCode.APPROVAL_EXPIRED: 409,
    ErrorCode.OPERATION_UNKNOWN: 409,
    ErrorCode.PROVIDER_QUOTA_EXHAUSTED: 503,
    ErrorCode.MODEL_CAPABILITY_UNAVAILABLE: 502,
}


class AppError(Exception):
    """Only a safe, static message may cross the API/log boundary."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int | None = None,
        *,
        retryable: bool = False,
        provider_request_id: str | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = HTTP_STATUS[code] if status_code is None else status_code
        self.retryable = retryable
        self.provider_request_id = provider_request_id
        self.provider_code = provider_code


class ConfigurationError(RuntimeError):
    """Configuration diagnostics must name fields, never print values or settings objects."""
