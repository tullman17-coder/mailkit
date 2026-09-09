"""Structured errors and process exit codes."""

from __future__ import annotations


class ExitCode:
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    AUTH = 4
    NETWORK = 5
    DAEMON = 6
    CONFLICT = 7
    RATE_LIMIT = 8


class MailkitError(Exception):
    exit_code = ExitCode.ERROR
    code = "error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "schema": "mailkit.error.v1",
            "code": self.code,
            "message": str(self),
            "details": self.details,
        }


class UsageError(MailkitError):
    exit_code = ExitCode.USAGE
    code = "usage"


class NotFoundError(MailkitError):
    exit_code = ExitCode.NOT_FOUND
    code = "not_found"


class AuthError(MailkitError):
    exit_code = ExitCode.AUTH
    code = "auth"


class NetworkError(MailkitError):
    exit_code = ExitCode.NETWORK
    code = "network"


class DaemonError(MailkitError):
    exit_code = ExitCode.DAEMON
    code = "daemon"


class ConflictError(MailkitError):
    exit_code = ExitCode.CONFLICT
    code = "conflict"


class RateLimitError(MailkitError):
    exit_code = ExitCode.RATE_LIMIT
    code = "rate_limit"


class ConfigError(MailkitError):
    exit_code = ExitCode.USAGE
    code = "config"


_CODE_TYPES: dict[str, type[MailkitError]] = {
    "usage": UsageError,
    "not_found": NotFoundError,
    "auth": AuthError,
    "network": NetworkError,
    "daemon": DaemonError,
    "conflict": ConflictError,
    "rate_limit": RateLimitError,
    "config": ConfigError,
}

_STATUS_TYPES: dict[int, type[MailkitError]] = {
    401: AuthError,
    403: AuthError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def from_api_error(
    message: str,
    *,
    code: str | None = None,
    details: dict | None = None,
    status: int | None = None,
) -> MailkitError:
    """Map a daemon JSON error.code and/or HTTP status onto a typed MailkitError."""
    details = dict(details or {})
    resolved = code or details.get("code") or ""
    cls = _CODE_TYPES.get(resolved)
    if cls is None and status is not None:
        cls = _STATUS_TYPES.get(status)
        if cls is None and status >= 500:
            cls = DaemonError
    return (cls or MailkitError)(message, details=details)
