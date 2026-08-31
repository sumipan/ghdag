"""ghdag.core.exceptions — shared exception base for ghdag."""


class GhdagError(Exception):
    """ghdag 共通基底例外。"""

    pass


class GitHubApiError(GhdagError):
    """GitHub API 操作の共通基底。status_code と message を保持する。"""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class AuthError(GitHubApiError):
    """認証失敗（401、トークン未設定）。"""


class RateLimitError(GitHubApiError):
    """レート制限超過（403 + X-RateLimit-Remaining: 0）。"""


class PermissionDeniedError(GitHubApiError):
    """権限不足（403、404 private repo）。"""


class NetworkError(GitHubApiError):
    """接続タイムアウト・DNS 解決失敗等のネットワークエラー。"""
