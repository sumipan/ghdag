"""ghdag.exceptions — shared exception base for ghdag (re-export shim)."""

from ghdag.core.exceptions import (
    AuthError,
    GhdagError,
    GitHubApiError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)

__all__ = [
    "GhdagError",
    "GitHubApiError",
    "AuthError",
    "RateLimitError",
    "PermissionDeniedError",
    "NetworkError",
]
