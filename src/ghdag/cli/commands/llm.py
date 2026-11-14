"""ghdag llm command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ghdag.config.env import ghdag_audit_path
from ghdag.core.exceptions import CliUsageError


def _llm_result_envelope(result) -> dict[str, object]:
    failure = result.failure_class
    return {
        "body": result.body,
        "ok": result.ok,
        "returncode": result.returncode,
        "failure_class": failure.value if failure is not None else None,
        "engine_used": result.engine_used,
        "model_used": result.model_used,
        "attempts": result.attempts,
        "session_id": result.session_id,
    }


def cmd_llm(args) -> None:
    """ghdag llm: one-shot LLM call (no workflow required)."""
    from ghdag.llm.engines import call, list_engines, list_models, validate_engine_model

    if args.list_engines:
        for engine in list_engines():
            print(engine)
        return

    if args.list_models:
        for model in list_models(args.engine):
            print(model)
        return

    prompt = args.prompt
    if prompt is None:
        if sys.stdin.isatty():
            raise CliUsageError(
                "prompt required (positional arg or stdin)",
                code="LLM_PROMPT_REQUIRED",
                hint="pass the prompt as an argument or on stdin",
            )
        prompt = sys.stdin.read().strip()
        if not prompt:
            raise CliUsageError(
                "empty prompt from stdin",
                code="LLM_PROMPT_REQUIRED",
                hint="pass the prompt as an argument or on stdin",
            )

    stdin_text = None
    if args.read_stdin and not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    perm = None
    if args.permission is not None:
        from ghdag.core.capabilities import PERMISSIONS
        perm = PERMISSIONS[args.permission]

    call_kwargs: dict = dict(
        route=[(args.engine, args.model)],
        timeout=args.timeout,
        stdin_text=stdin_text,
    )
    if perm is not None:
        call_kwargs["permission"] = perm

    result = call(prompt, **call_kwargs)

    if args.output_json:
        print(json.dumps(_llm_result_envelope(result)))
    else:
        if result.body:
            print(result.body, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

    audit_path = args.audit_path or ghdag_audit_path()
    if audit_path and result.ok:
        from ghdag.io.audit import (
            compute_prompt_hash,
            write_llm_audit_log,
            write_llm_inference_audit,
        )
        resolved_model = validate_engine_model(args.engine, args.model)
        write_llm_audit_log(
            Path(audit_path),
            engine=args.engine,
            model=resolved_model,
            exit_code=result.returncode,
            correlation_id=args.correlation_id,
            timeout_sec=args.timeout,
            request_id=args.request_id,
        )
        if result.latency_ms > 0:
            write_llm_inference_audit(
                Path(audit_path),
                prompt_hash=compute_prompt_hash(prompt),
                latency_ms=result.latency_ms,
                engine=args.engine,
                model=resolved_model,
                correlation_id=args.correlation_id,
            )

    sys.exit(result.returncode)
