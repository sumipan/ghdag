"""ghdag.core.container — docker run prefix for ``sandbox="container"`` commands."""

from __future__ import annotations

import os
import shlex

from ghdag.core.capabilities import LLMCapabilities

__all__ = ["CONTAINER_WORKDIR", "container_prefix"]

CONTAINER_WORKDIR = "/work"


def container_prefix(
    capabilities: LLMCapabilities, *, workdir: str = '"$PWD"'
) -> list[str]:
    """Build the shell tokens that run the engine CLI inside a container.

    Only ``workdir`` (mounted at ``/work``), ``container_mounts`` and the
    environment variable names in ``container_env`` are passed to the
    container. Environment values never appear on argv (``-e NAME`` only).

    ``workdir`` is emitted verbatim (it is shell syntax such as ``"$PWD"``);
    every other value is shell-quoted.

    Raises:
        ValueError: ``container_image`` is empty or an env entry is not a bare name.
    """
    if not capabilities.container_image:
        raise ValueError("sandbox='container' requires a non-empty container_image")

    docker_bin = capabilities.docker_bin
    tokens: list[str] = []
    if "/" in docker_bin:
        bin_dir = os.path.dirname(docker_bin)
        tokens += ["env", f"PATH={shlex.quote(bin_dir)}:$PATH"]

    work_mount = f"{workdir}:{CONTAINER_WORKDIR}"
    if capabilities.container_readonly:
        work_mount += ":ro"
    tokens += [
        shlex.quote(docker_bin), "run", "--rm", "-i",
        "-v", work_mount, "-w", CONTAINER_WORKDIR,
    ]
    if capabilities.container_readonly:
        tokens.append("--read-only")

    for mount in capabilities.container_mounts:
        tokens += ["-v", shlex.quote(os.path.expanduser(mount))]
    for name in capabilities.container_env:
        if not name or "=" in name:
            raise ValueError(
                f"container_env entries must be variable names, got {name!r}"
            )
        tokens += ["-e", shlex.quote(name)]

    tokens.append(shlex.quote(capabilities.container_image))
    return tokens
