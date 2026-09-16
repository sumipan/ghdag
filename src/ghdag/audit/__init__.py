"""ghdag.audit — audit envelope public APIs (latency span, etc.)."""

from ghdag.audit.span import emit_span, make_span_id

__all__ = ["emit_span", "make_span_id"]
