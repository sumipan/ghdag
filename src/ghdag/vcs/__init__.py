"""ghdag.vcs — single git sink for commit/push (nexus Issue #3831)."""

from ghdag.vcs.factory import get_sink, git_enabled
from ghdag.vcs.local import LocalGitSink
from ghdag.vcs.sink import CommitResult, ConflictError, GitSink, NullSink, OwnershipError

__all__ = [
    "CommitResult",
    "ConflictError",
    "GitSink",
    "LocalGitSink",
    "NullSink",
    "OwnershipError",
    "get_sink",
    "git_enabled",
]
