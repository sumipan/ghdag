"""ghdag.io — filesystem I/O facade (audit / rotate / exec.jsonl)."""

from ghdag.io import audit, audit_query, exec_jsonl

__all__ = ["audit", "audit_query", "exec_jsonl"]
