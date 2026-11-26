"""Tests for ghdag.config.env — environment variable accessors (issue #3039)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.config import env


def test_github_token_prefers_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("GH_TOKEN", "legacy")
    assert env.github_token() == "gh-token"


def test_github_token_falls_back_to_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "legacy")
    assert env.github_token() == "legacy"


def test_github_token_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert env.github_token() is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("1", True),
        ("0", False),
        ("off", True),
        (" 0 ", False),
    ],
)
def test_run_recorder_enabled_only_zero_disables(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: bool,
) -> None:
    """Refs sumipan/ghdag#8060 AC1; host pre-migrated in sumipan/nexus#8059 (``6a08e38``)."""
    monkeypatch.delenv("GHDAG_RUN_RECORDER", raising=False)
    if value is not None:
        monkeypatch.setenv("GHDAG_RUN_RECORDER", value)
    assert env.run_recorder_enabled() is expected
    assert "run_recorder_enabled" in env.__all__


def test_engine_isolation_enabled_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_ENGINE_ISOLATION", raising=False)
    assert env.engine_isolation_enabled() is False
    assert "engine_isolation_enabled" in env.__all__


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "YES", " 1 "])
def test_enable_git_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert env.enable_git() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  ", "on"])
def test_enable_git_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ENABLE_GIT", value)
    assert env.enable_git() is False


def test_enable_git_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_GIT", raising=False)
    assert env.enable_git() is False


def test_state_dir_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "state"))
    assert env.ghdag_state_dir() == str(tmp_path / "state")
    assert env.state_dir("jobs") == tmp_path / "state"


_ENV_ACCESSOR_CASES: list[tuple[str, str, object, object]] = [
  # name, env var, env value (None=unset), expected or exception type
    ("quota_default_pause_seconds", "GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS", None, 18000),
    ("quota_default_pause_seconds", "GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS", "3600", 3600),
    ("quota_default_pause_seconds", "GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS", "x", ValueError),
    ("ghdag_rate_limit_max_wait_sec", "GHDAG_RATE_LIMIT_MAX_WAIT_SEC", None, 900),
    ("ghdag_rate_limit_max_wait_sec", "GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "", 900),
    ("ghdag_rate_limit_max_wait_sec", "GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "  ", 900),
    ("ghdag_rate_limit_max_wait_sec", "GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "120", 120),
    ("ghdag_rate_limit_max_wait_sec", "GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "nope", 900),
    ("ghdag_etag_cache", "GHDAG_ETAG_CACHE", None, None),
    ("ghdag_etag_cache", "GHDAG_ETAG_CACHE", "/tmp/etag", "/tmp/etag"),
    ("ghdag_forge", "GHDAG_FORGE", None, None),
    ("ghdag_forge", "GHDAG_FORGE", " GitHub ", " GitHub "),
    ("ghdag_forge_root", "GHDAG_FORGE_ROOT", None, None),
    ("ghdag_forge_root", "GHDAG_FORGE_ROOT", "/data", "/data"),
    ("ghdag_exec_jsonl", "GHDAG_EXEC_JSONL", None, None),
    ("ghdag_exec_jsonl", "GHDAG_EXEC_JSONL", "jobs/exec.jsonl", "jobs/exec.jsonl"),
]


@pytest.mark.parametrize(
    ("accessor", "var_name", "var_value", "expected"),
    _ENV_ACCESSOR_CASES,
    ids=[c[0] + "_" + str(c[2]) for c in _ENV_ACCESSOR_CASES],
)
def test_central_env_accessors(
    monkeypatch: pytest.MonkeyPatch,
    accessor: str,
    var_name: str,
    var_value: object,
    expected: object,
) -> None:
    """Refs sumipan/ghdag#7545 — six ghdag-owned env vars via config.env."""
    monkeypatch.delenv(var_name, raising=False)
    if var_value is not None:
        monkeypatch.setenv(var_name, str(var_value))
    fn = getattr(env, accessor)
    if isinstance(expected, type) and issubclass(expected, BaseException):
        with pytest.raises(expected):
            fn()
        return
    assert fn() == expected


_TARGET_ENV_VARS = frozenset(
    {
        "GHDAG_QUOTA_DEFAULT_PAUSE_SECONDS",
        "GHDAG_RATE_LIMIT_MAX_WAIT_SEC",
        "GHDAG_ETAG_CACHE",
        "GHDAG_FORGE",
        "GHDAG_FORGE_ROOT",
        "GHDAG_EXEC_JSONL",
    }
)


def test_ghdag_owned_env_reads_only_in_config_env() -> None:
    """Refs sumipan/ghdag#7545 — os.environ reads for six vars live only in env.py."""
    src = Path(__file__).resolve().parents[2] / "src" / "ghdag"
    env_py = src / "config" / "env.py"
    offenders: list[str] = []
    for py in sorted(src.rglob("*.py")):
        if py == env_py:
            continue
        rel = py.relative_to(src)
        for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if not any(name in line for name in _TARGET_ENV_VARS):
                continue
            if "os.environ" not in line and "os.getenv" not in line:
                continue
            offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == []


