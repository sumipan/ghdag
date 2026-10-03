"""ghdag.llm._constants — default values for engines and models"""

from __future__ import annotations

# Fallback defaults (used when no YAML config file exists)
DEFAULT_ENGINE_MODELS: dict[str, list[str]] = {
    "claude": [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    # cursor agent CLI (https://cursor.com). Invoked as `agent --model <id> -p <prompt>`.
    # Available models can be listed with `agent --list-models`. Only representative ones are allowlisted.
    "cursor": [
        "auto",
        "composer-2",
        "composer-2-fast",
        "gpt-5.2",
        "gpt-5.3-codex",
        "gpt-5.3-codex-fast",
        "gpt-5.3-codex-high",
        "gpt-5.3-codex-high-fast",
        "gpt-5.4-medium-fast",
    ],
    # The shell engine runs the bash script at order_path directly. It does not call an LLM.
    "shell": [
        "bash",
    ],
    # Allowed models for codex CLI (https://github.com/openai/codex).
    # Measured with ChatGPT account auth (2026-08-11 / codex-cli 0.147.0).
    # The accepted set may differ under API key auth.
    "codex": [
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
    ],
}
