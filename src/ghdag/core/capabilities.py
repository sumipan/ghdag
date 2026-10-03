"""ghdag.core.capabilities — capability-constraint value object and presets for LLM calls"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMCapabilities:
    """Value object bundling the capability constraints of an LLM call."""
    permission_mode: str = "default"
    output_format: str = "text"  # "text" | "json"
    allowed_tools: tuple[str, ...] = ()  # empty = unspecified (not passed to the CLI)
    disallowed_tools: tuple[str, ...] = ()  # empty = unspecified (not passed to the CLI)
    stream: bool = False  # when True, stream output (claude/cursor: stream-json, codex: --json)
    sandbox: str = "off"  # "off" | "readonly" | "container"
    resume: bool = False  # when True, allow the session-resume flow
    # Container sandbox (sandbox="container"); see ghdag.core.container.container_prefix.
    container_image: str = ""  # required when sandbox="container"
    container_readonly: bool = False  # --read-only root FS and read-only worktree mount
    container_mounts: tuple[str, ...] = ()  # extra "host:container[:ro]" mounts; "~" expanded
    container_env: tuple[str, ...] = ()  # env var names passed through (values stay off argv)
    docker_bin: str = "docker"  # docker executable; a path also prepends its dir to PATH


TEXT_ONLY = LLMCapabilities(
    permission_mode="default",
    output_format="text",
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"),
)

JSON_ONLY = LLMCapabilities(
    permission_mode="default",
    output_format="json",
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"),
)

WEB_RESEARCH = LLMCapabilities(
    permission_mode="default",
    output_format="text",
    allowed_tools=("WebFetch", "WebSearch", "Read", "Grep", "Glob"),
    disallowed_tools=("Bash", "Edit", "Write", "NotebookEdit"),
)

DANGEROUS_FULL_ACCESS = LLMCapabilities(
    permission_mode="bypassPermissions",
    output_format="text",
)

# Allow observational Bash while sealing edits with the sandbox (an alternative to
# TEXT_ONLY's tool stripping). disallowed_tools is a second line of defense for claude.
# On codex / cursor it is a noop via each engine's _IGNORED_CAPABILITIES, but the
# preset definition stays engine-agnostic.
# Write is not disallowed: in claude's plan mode (sandbox=readonly) the harness has
# Write the plan file (~/.claude/plans/*.md), and plan mode itself rejects Write to
# anything other than the plan file (sumipan/nexus#3188).
READONLY_OBSERVE = LLMCapabilities(
    sandbox="readonly",
    disallowed_tools=("Edit", "NotebookEdit"),
)

PRESETS: dict[str, LLMCapabilities] = {
    "text_only": TEXT_ONLY,
    "json_only": JSON_ONLY,
    "web_research": WEB_RESEARCH,
    "dangerous_full_access": DANGEROUS_FULL_ACCESS,
    "readonly_observe": READONLY_OBSERVE,
}
