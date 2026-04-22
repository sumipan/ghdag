"""ghdag.shr.daemon — overmind (Procfile) ベースのプロセス管理。

Procfile にエントリを追加・削除し、overmind コマンドで起動・停止を制御する。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

PROCFILE_PATH: Path = Path("Procfile")
PROCESS_NAME = "shr_runner"


def _portable_runner_path(runner_dir: Path) -> str:
    """`runner_dir` を Procfile に書き込む際の表現を返す。

    `~` 配下なら `$HOME/...` 形式に置換することで、ホームパスが異なる
    別マシンにリポジトリを同期しても破綻しない Procfile になる。
    `~` 配下でない場合のみ絶対パスをそのまま使う。
    """
    home = Path.home()
    try:
        rel = runner_dir.resolve().relative_to(home.resolve())
    except ValueError:
        return str(runner_dir)
    return f"$HOME/{rel.as_posix()}"


def _build_procfile_entry(runner_dir: Path, process_name: str) -> str:
    """Procfile に追加する1行を生成する。"""
    runner_expr = _portable_runner_path(runner_dir)
    return f"{process_name}: {runner_expr}/run.sh 2>&1 | python3 scripts/log-timestamp.py"


def install_procfile_entry(runner_dir: Path, process_name: str) -> str:
    """Procfile に runner エントリを追加する。

    Args:
        runner_dir: actions-runner が展開されたディレクトリ
        process_name: overmind プロセス名（例: "shr_runner"）

    Returns:
        追加されたプロセス名

    Raises:
        RuntimeError: 既に同名エントリが存在する場合
    """
    procfile = PROCFILE_PATH
    entry = _build_procfile_entry(runner_dir, process_name)

    if procfile.exists():
        content = procfile.read_text(encoding="utf-8")
        pattern = rf"^{re.escape(process_name)}:"
        if re.search(pattern, content, re.MULTILINE):
            raise RuntimeError(f"Procfile に既に '{process_name}' エントリが存在します")
        if not content.endswith("\n"):
            content += "\n"
    else:
        content = ""

    content += f"\n# Self-hosted runner (ghdag shr)\n{entry}\n"
    procfile.write_text(content, encoding="utf-8")
    return process_name


def uninstall_procfile_entry(process_name: str) -> None:
    """Procfile から runner エントリを削除する。存在しない場合は無視する。"""
    procfile = PROCFILE_PATH
    if not procfile.exists():
        return

    lines = procfile.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = rf"^{re.escape(process_name)}:"
    comment_pattern = r"^# Self-hosted runner \(ghdag shr\)$"

    filtered: list[str] = []
    skip_next = False
    for line in lines:
        stripped = line.rstrip("\n")
        if re.match(comment_pattern, stripped):
            skip_next = True
            continue
        if skip_next and re.match(pattern, stripped):
            skip_next = False
            continue
        if re.match(pattern, stripped):
            continue
        skip_next = False
        filtered.append(line)

    procfile.write_text("".join(filtered), encoding="utf-8")


def start(process_name: str) -> None:
    """overmind restart でプロセスを起動する。

    Raises:
        subprocess.CalledProcessError: overmind が失敗した場合
    """
    subprocess.run(
        ["overmind", "restart", process_name],
        check=True,
    )


def stop(process_name: str) -> None:
    """overmind stop でプロセスを停止する。

    Raises:
        subprocess.CalledProcessError: overmind が失敗した場合
    """
    subprocess.run(
        ["overmind", "stop", process_name],
        check=True,
    )


def is_running(process_name: str) -> bool:
    """overmind status でプロセスが起動中か確認する。"""
    result = subprocess.run(
        ["overmind", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if line.strip().startswith(process_name):
            return "running" in line.lower()
    return False
