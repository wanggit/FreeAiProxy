from __future__ import annotations


class GatewayError(Exception):
    status = 500
    error_type = "internal_error"
    code: str | None = None

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code if code is not None else self.code

    def to_payload(self) -> dict:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": None,
                "code": self.code,
            }
        }


class InvalidRequestError(GatewayError):
    status = 400
    error_type = "invalid_request_error"
    code = "invalid_request"


class UnauthorizedError(GatewayError):
    status = 401
    error_type = "invalid_request_error"
    code = "invalid_api_key"


class SessionInvalidError(GatewayError):
    status = 404
    error_type = "invalid_request_error"
    code = "session_invalid"


class HistoryConflictError(GatewayError):
    status = 400
    error_type = "invalid_request_error"
    code = "history_conflict"


class QueueFullError(GatewayError):
    status = 429
    error_type = "rate_limit_error"
    code = "queue_full"


class UpstreamError(GatewayError):
    status = 502
    error_type = "upstream_error"
    code = "upstream_error"


class NotLoggedInError(UpstreamError):
    status = 502
    error_type = "authentication_required"
    code = "login_required"


class UpstreamPageChangedError(UpstreamError):
    status = 500
    error_type = "upstream_page_changed"
    code = "page_changed"


class ChatNotFoundError(UpstreamError):
    status = 404
    error_type = "invalid_request_error"
    code = "session_dead"


class UpstreamRateLimitedError(GatewayError):
    status = 429
    error_type = "upstream_rate_limited"
    code = "upstream_rate_limited"


class UpstreamTimeoutError(GatewayError):
    status = 200
    error_type = "upstream_timeout"
    code = "first_token_timeout"
