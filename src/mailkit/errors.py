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
