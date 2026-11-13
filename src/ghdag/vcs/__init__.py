"""ghdag.vcs — single git sink for commit/push."""

from ghdag.vcs.factory import get_sink, git_enabled
from ghdag.vcs.local import LocalGitSink
from ghdag.vcs.sink import CommitResult, ConflictError, NullSink, OwnershipError

__all__ = [
    "CommitResult",
    "ConflictError",
    "LocalGitSink",
    "NullSink",
    "OwnershipError",
    "get_sink",
    "git_enabled",
]
