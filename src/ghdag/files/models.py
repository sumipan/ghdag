"""File operation models (re-export shim)."""

from ghdag.core.models.files import (
    AppendResult,
    AppendStatus,
    MdFile,
    PathTraversalError,
    PromoteResult,
    PromoteStatus,
    WriteResult,
)

__all__ = [
    "PathTraversalError",
    "MdFile",
    "AppendStatus",
    "AppendResult",
    "WriteResult",
    "PromoteStatus",
    "PromoteResult",
]
