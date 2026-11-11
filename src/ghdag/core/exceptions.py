"""ghdag.core.exceptions — shared exception base for ghdag."""

from __future__ import annotations

from datetime import UTC, datetime


class GhdagError(Exception):
    """Common base exception for ghdag."""

    code = "GHDAG_ERROR"
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code if code is not None else type(self).code
        self.hint = hint

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "exit_code": type(self).exit_code,
        }


class ConfigError(GhdagError, ValueError):
    """Invalid configuration or input validation failure."""

    code = "CONFIG_INVALID"
    exit_code = 3


class CliUsageError(GhdagError):
    """CLI argument combination or usage error."""

    code = "CLI_USAGE"
    exit_code = 2


class GitHubApiError(GhdagError):
    """Common base for GitHub API operations. Holds status_code and message."""

    code = "GITHUB_API_ERROR"
    exit_code = 4

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message, code=code, hint=hint)


class AuthError(GitHubApiError):
    """Authentication failure (401, token not set)."""

    code = "GITHUB_AUTH"


class RateLimitError(GitHubApiError):
    """Rate limit exceeded (403 + X-RateLimit-Remaining: 0)."""

    code = "GITHUB_RATE_LIMIT"

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        reset_at: int | None = None,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.reset_at = reset_at
        if hint is None and reset_at is not None:
            hint = datetime.fromtimestamp(reset_at, tz=UTC).isoformat()
        super().__init__(message, status_code=status_code, code=code, hint=hint)


class PermissionDeniedError(GitHubApiError):
    """Insufficient permissions (403, 404 private repo)."""

    code = "GITHUB_PERMISSION_DENIED"


class NetworkError(GitHubApiError):
    """Network error such as connection timeout or DNS resolution failure."""

    code = "GITHUB_NETWORK"


class LLMParseError(GhdagError):
    """Raised when a response violates the output_format contract."""

    code = "LLM_OUTPUT_NOT_JSON"
    exit_code = 3

    def __init__(self, raw: str, reason: str):
        self.raw = raw
        self.reason = reason
        super().__init__(
            f"LLM output parse failed: {reason}",
            hint="Return exactly one JSON object; surrounding prose and a code fence are allowed",
        )
