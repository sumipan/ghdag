"""call() guards cursor's stuck shell-state ``cat`` readers (sumipan/nexus#5141)."""

from __future__ import annotations

import signal
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

import ghdag.llm.engines as engines
from ghdag.core.models.metrics import FailureClass
from ghdag.llm.engines import call

ROOT_PID = 100
CAT_PID = 300

_STALLED_PS = (
    f"  {ROOT_PID}    1 0:00.10 agent agent -p\n"
    f"  200  {ROOT_PID} 0:00.00 zsh zsh -c snap=$(command cat <&3); echo\n"
    f"  {CAT_PID}  200 0:00.00 cat cat\n"
)
_BUSY_PS = (
    f"  {ROOT_PID}    1 0:00.10 agent agent -p\n"
    f"  200  {ROOT_PID} 0:00.00 zsh zsh -c snap=$(command cat <&3); echo\n"
    f"  {CAT_PID}  200 0:00.05 cat cat\n"
)


class FakeProc:
    """Popen stand-in whose communicate sleeps ``timeout`` and raises N times."""

    def __init__(
        self,
        cmd: list[str],
        *,
        timeouts_before_done: int,
        returncode: int = 0,
        result: tuple[str, str] = ("out", "err"),
    ) -> None:
        self.cmd = cmd
        self.pid = ROOT_PID
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._remaining = timeouts_before_done
        self._result = result
        self.calls: list[dict] = []
        self.kill = MagicMock(side_effect=self._on_kill)

    def _on_kill(self) -> None:
        self._remaining = 0
        self._final_returncode = -9

    def communicate(self, input=None, timeout=None):  # noqa: A002 — mirrors Popen
        self.calls.append({"input": input, "timeout": timeout})
        if self._remaining is None or self._remaining > 0:
            if timeout is not None:
                time.sleep(timeout)
            if self._remaining is not None:
                self._remaining -= 1
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.returncode = self._final_returncode
        return self._result


@pytest.fixture
def guard_env(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    monkeypatch.delenv("GHDAG_ENGINE_STALL_GUARD", raising=False)
    monkeypatch.setenv("GHDAG_ENGINE_STALL_SEC", "0.1")
    monkeypatch.setenv("GHDAG_ENGINE_STALL_INTERVAL_SEC", "0.05")
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(engines, "_stall_kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(engines, "_stall_ps", lambda: _STALLED_PS)
    return kills


def _patch_popen(proc_factory):
    holder: dict = {}

    def fake_popen(cmd, **kwargs):
        holder["kwargs"] = kwargs
        holder["proc"] = proc_factory(cmd)
        return holder["proc"]

    return patch("ghdag.llm.engines.subprocess.Popen", side_effect=fake_popen), holder


def test_guard_fires_and_kills_cat_once(guard_env: list[tuple[int, int]]) -> None:
    popen, holder = _patch_popen(
        lambda cmd: FakeProc(cmd, timeouts_before_done=2, returncode=3)
    )
    with popen, patch("ghdag.llm.engines.subprocess.run") as mock_run:
        r = call("p", engine="cursor", timeout=600)

    mock_run.assert_not_called()
    assert guard_env == [(CAT_PID, signal.SIGTERM)]
    assert r.returncode == 3
    assert r.stdout == "out"
    last = r.stderr.rstrip("\n").splitlines()[-1]
    assert last.startswith(f"[stall-guard] killed cat pid={CAT_PID}")
    assert r.stderr.startswith("err\n")


def test_input_passed_only_on_first_communicate(guard_env: list[tuple[int, int]]) -> None:
    popen, holder = _patch_popen(lambda cmd: FakeProc(cmd, timeouts_before_done=2))
    with popen:
        call("p", engine="cursor", timeout=600, stdin_text="extra")

    calls = holder["proc"].calls
    assert len(calls) == 3
    assert calls[0]["input"] == "p\n\nextra"
    assert all(c["input"] is None for c in calls[1:])
    assert holder["kwargs"]["stdin"] is subprocess.PIPE


def test_no_fire_when_cat_has_cpu_time(
    guard_env: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engines, "_stall_ps", lambda: _BUSY_PS)
    popen, _ = _patch_popen(lambda cmd: FakeProc(cmd, timeouts_before_done=4))
    with popen:
        r = call("p", engine="cursor", timeout=600)

    assert guard_env == []
    assert "[stall-guard]" not in r.stderr
    assert r.stderr == "err"


def test_guard_off_uses_subprocess_run(
    guard_env: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")
    ps = MagicMock(return_value=_STALLED_PS)
    kill = MagicMock()
    monkeypatch.setattr(engines, "_stall_ps", ps)
    monkeypatch.setattr(engines, "_stall_kill", kill)
    done = subprocess.CompletedProcess([], 0, "ok", "")
    with patch("ghdag.llm.engines.subprocess.run", return_value=done) as mock_run, patch(
        "ghdag.llm.engines.subprocess.Popen"
    ) as mock_popen:
        r = call("p", engine="cursor", timeout=600)

    assert mock_run.call_count == 1
    mock_popen.assert_not_called()
    ps.assert_not_called()
    kill.assert_not_called()
    assert r.stdout == "ok"


def test_claude_uses_subprocess_run(
    guard_env: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ps = MagicMock(return_value=_STALLED_PS)
    monkeypatch.setattr(engines, "_stall_ps", ps)
    done = subprocess.CompletedProcess([], 0, "ok", "")
    with patch("ghdag.llm.engines.subprocess.run", return_value=done) as mock_run, patch(
        "ghdag.llm.engines.subprocess.Popen"
    ) as mock_popen:
        call("p", engine="claude", timeout=600)

    assert mock_run.call_count == 1
    mock_popen.assert_not_called()
    ps.assert_not_called()


def test_timeout_kills_process_and_maps_to_124(
    guard_env: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engines, "_stall_ps", lambda: _BUSY_PS)
    popen, holder = _patch_popen(
        lambda cmd: FakeProc(cmd, timeouts_before_done=None, result=("partial", "e"))
    )
    with popen:
        r = call("p", engine="cursor", timeout=1)

    proc = holder["proc"]
    assert proc.kill.call_count == 1
    assert r.returncode == 124
    assert r.failure_class == FailureClass.TIMEOUT
    assert r.stdout == "partial"
    assert any(line.startswith("TIMEOUT: ") for line in r.stderr.splitlines())
    sliced = [c["timeout"] for c in proc.calls if c["timeout"] is not None]
    assert sum(sliced) <= 1.0 + 1e-6


@pytest.mark.parametrize(
    ("exc_type", "errno_", "returncode", "reason"),
    [
        (FileNotFoundError, 2, 127, "command not found"),
        (PermissionError, 13, 126, "permission denied"),
    ],
)
def test_launch_failure_maps_like_before(
    guard_env: list[tuple[int, int]],
    exc_type: type[OSError],
    errno_: int,
    returncode: int,
    reason: str,
) -> None:
    def boom(cmd, **kwargs):
        raise exc_type(errno_, "x", cmd[0])

    with patch("ghdag.llm.engines.subprocess.Popen", side_effect=boom) as mock_popen:
        r = call("p", engine="cursor", timeout=600)

    exe = mock_popen.call_args.args[0][0]
    assert r.returncode == returncode
    assert r.stderr == f"{exe}: {reason}\n"
    assert guard_env == []
