"""ghdag llm コマンド。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def cmd_llm(args) -> None:
    """ghdag llm: ワンショット LLM 呼び出し（ワークフロー不要）。"""
    from ghdag.llm.engines import (
        EngineModelError,
        call,
        list_engines,
        list_models,
    )

    if args.list_engines:
        for engine in list_engines():
            print(engine)
        return

    if args.list_models:
        try:
            for model in list_models(args.engine):
                print(model)
        except EngineModelError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    prompt = args.prompt
    if prompt is None:
        if sys.stdin.isatty():
            print("error: prompt required (positional arg or stdin)", file=sys.stderr)
            sys.exit(1)
        prompt = sys.stdin.read().strip()
        if not prompt:
            print("error: empty prompt from stdin", file=sys.stderr)
            sys.exit(1)

    stdin_text = None
    if args.read_stdin and not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    capabilities = None
    if args.capabilities_preset is not None or args.permission_mode is not None:
        from ghdag.llm.capabilities import PRESETS, LLMCapabilities
        if args.capabilities_preset is not None:
            capabilities = PRESETS[args.capabilities_preset]
        else:
            capabilities = LLMCapabilities()
        if args.permission_mode is not None:
            import dataclasses
            capabilities = dataclasses.replace(capabilities, permission_mode=args.permission_mode)

    call_kwargs: dict = dict(
        engine=args.engine,
        model=args.model,
        timeout=args.timeout,
        stdin_text=stdin_text,
        dangerously_skip_permissions=args.dangerously_skip_permissions,
    )
    if capabilities is not None:
        call_kwargs["capabilities"] = capabilities

    try:
        result = call(prompt, **call_kwargs)
    except EngineModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    audit_path = args.audit_path or os.environ.get("GHDAG_AUDIT_PATH")
    if audit_path and result.ok:
        from ghdag.llm.engines import validate_engine_model
        from ghdag.pipeline.audit import (
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
