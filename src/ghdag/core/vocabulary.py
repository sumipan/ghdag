"""Shared vocabulary for queue/done/fan-out file conventions.

Behavior-preserving constants: values must match the historical stringly-typed
markers used across the workflow→pipeline and dag towers.
"""

from __future__ import annotations

import re

# --- done markers (jobs/done/<uuid> contents) ---
DONE_SUCCESS = "0"
DONE_REJECTED = "REJECTED"
DONE_REJECTED_FINAL = "REJECTED_FINAL"
DONE_ENGINE_ERROR = "ENGINE_ERROR"
DONE_ENGINE_ERROR_FINAL = "ENGINE_ERROR_FINAL"
DONE_TIMEOUT = "TIMEOUT"
DONE_DEP_FAILED = "DEP_FAILED"
DONE_EMPTY_RESULT = "EMPTY_RESULT"
DONE_PIPELINE_FAILED_PREFIX = "PIPELINE_FAILED:"
DONE_FANOUT_CHILD_FAILED = "FANOUT_CHILD_FAILED"
DONE_FANOUT_PARSE_FAILED = "FANOUT_PARSE_FAILED"
DONE_SKIPPED_MISSING_INPUT = "SKIPPED_MISSING_INPUT"
DONE_UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
DONE_ORPHAN_ARCHIVED = "ORPHAN_ARCHIVED"

# --- queue file naming ---
QUEUE_FILE_RE = re.compile(
    r"^(\d{14})-([\w-]+)-(order|result|stderr)-([a-fA-F0-9\-]{36})\.md$"
)

# --- pipeline status line in result files ---
PIPELINE_STATUS_RE = re.compile(r"^PIPELINE_STATUS:\s*(\S+)\s*$", re.MULTILINE)

# --- fan-out conventions ---
FANOUT_SEPARATOR = "--fo--"
FANOUT_ANCHOR = "ghdag_fanout:"
FANOUT_KEY = "ghdag_fanout"
