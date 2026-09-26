"""Tests for ghdag.core.container.container_prefix."""

from __future__ import annotations

import os

import pytest

from ghdag.core.capabilities import LLMCapabilities
from ghdag.core.container import container_prefix


def _caps(**kwargs: object) -> LLMCapabilities:
    kwargs.setdefault("container_image", "img:1")
    return LLMCapabilities(sandbox="container", **kwargs)  # type: ignore[arg-type]


def test_full_prefix_with_docker_path() -> None:
    caps = _caps(
        container_mounts=("/h/.codex:/root/.codex:ro",),
        container_env=("CURSOR_API_KEY",),
        docker_bin="/opt/d/bin/docker",
    )
    assert " ".join(container_prefix(caps)) == (
        'env PATH=/opt/d/bin:$PATH /opt/d/bin/docker run --rm -i -v "$PWD":/work '
        "-w /work -v /h/.codex:/root/.codex:ro -e CURSOR_API_KEY img:1"
    )


def test_bare_docker_bin_has_no_path_prefix() -> None:
    assert container_prefix(_caps()) == [
        "docker", "run", "--rm", "-i", "-v", '"$PWD":/work', "-w", "/work", "img:1",
    ]


def test_empty_image_raises() -> None:
    with pytest.raises(ValueError, match="container_image"):
        container_prefix(_caps(container_image=""))


def test_readonly_adds_read_only_and_ro_worktree_mount() -> None:
    prefix = container_prefix(_caps(container_readonly=True))
    assert "--read-only" in prefix
    assert '"$PWD":/work:ro' in prefix
    assert '"$PWD":/work' not in prefix


def test_custom_workdir() -> None:
    prefix = container_prefix(_caps(), workdir="/tmp/wt")
    assert "/tmp/wt:/work" in prefix


def test_env_values_never_on_argv() -> None:
    with pytest.raises(ValueError, match="container_env"):
        container_prefix(_caps(container_env=("TOKEN=secret",)))


def test_tilde_mount_expanded_to_home() -> None:
    prefix = container_prefix(_caps(container_mounts=("~/.codex:/root/.codex:ro",)))
    home = os.path.expanduser("~")
    assert f"{home}/.codex:/root/.codex:ro" in " ".join(prefix)


def test_no_host_secrets_mounted_by_default() -> None:
    joined = " ".join(container_prefix(_caps()))
    for secret in (".ssh", ".config", ".env"):
        assert secret not in joined
    # Only the worktree is mounted.
    assert joined.count("-v ") == 1


def test_values_are_shell_quoted() -> None:
    prefix = container_prefix(_caps(container_image="img:1; rm -rf /"))
    assert prefix[-1] == "'img:1; rm -rf /'"
