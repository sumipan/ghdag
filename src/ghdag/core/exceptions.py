"""ghdag.core.exceptions — shared exception base for ghdag."""


class GhdagError(Exception):
    """Common base exception for ghdag."""

    pass


class GitHubApiError(GhdagError):
    """Common base for GitHub API operations. Holds status_code and message."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class AuthError(GitHubApiError):
    """Authentication failure (401, token not set)."""


class RateLimitError(GitHubApiError):
    """Rate limit exceeded (403 + X-RateLimit-Remaining: 0)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        reset_at: int | None = None,
    ):
        self.reset_at = reset_at
        super().__init__(message, status_code=status_code)


class PermissionDeniedError(GitHubApiError):
    """Insufficient permissions (403, 404 private repo)."""


class NetworkError(GitHubApiError):
    """Network error such as connection timeout or DNS resolution failure."""
