from deephelp_app.domain.models import ErrorCode


class AppError(Exception):
    """Only a safe, static message may cross the API/log boundary."""

    def __init__(
        self, code: ErrorCode, message: str, status_code: int = 500, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code
        self.retryable = retryable


class ConfigurationError(RuntimeError):
    """Configuration diagnostics must name fields, never print values or settings objects."""
