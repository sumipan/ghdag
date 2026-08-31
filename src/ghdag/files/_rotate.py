"""files/_rotate.py — re-export shim for ghdag.io._rotate (nexus Issue #2673)."""

from __future__ import annotations

from ghdag.io._rotate import _MAX_AUDIT_BYTES, _do_rotate, _maybe_rotate

__all__ = ["_MAX_AUDIT_BYTES", "_do_rotate", "_maybe_rotate"]
