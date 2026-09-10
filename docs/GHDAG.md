# ghdag — package notes

Implementation code under `src/ghdag/` is the source of truth. Nexus host docs live in the nexus repository (`docs/GHDAG.md`); this file tracks package-local reference material.

## Environment variables

All reads go through `ghdag.config.env`. Other modules must not call `os.environ.get` for these keys.

| Variable | Accessor | Default | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | `github_token()` | unset | GitHub REST/GraphQL auth (preferred) |
| `GH_TOKEN` | `github_token()` | unset | Fallback when `GITHUB_TOKEN` is unset |
| `GITHUB_REPOSITORIES` | `github_repositories_raw()` | `""` | Comma-separated `owner/repo` list |
| `GHDAG_LLM_MODELS` | `ghdag_llm_models()` | unset | Path to engine model allowlist YAML |
| `GHDAG_SAFE_DEFAULT_PERMISSION` | `ghdag_safe_default_permission()` | unset → `text_only` | Permission preset when step has none |
| `GHDAG_SESSION_COMPACTION` | `session_compaction_enabled()` | off | Opt-in session compaction (`1`/`true`/`yes`/`on`) |
| `GHDAG_AUDIT_PATH` | `ghdag_audit_path()` | unset | Override audit.jsonl path (`ghdag llm` / UI) |
| `GHDAG_TOKEN_WARN_THRESHOLD` | `ghdag_token_warn_threshold()` | `500000` | UI token-usage warning threshold |

## Cleanup notes (nexus #3039)

Retained despite earlier “unused” candidates:

- `metrics.MetricsRecorder` — used by nexus `scripts/diary_hooks.py` and issuesmith
- `markdown.body_editor` — used by issuesmith
- `io.sessions` — used by nexus `tools/mltgnt_bridge/`
