"""ghdag.shr.daemon — launchd plist の生成・配置・制御。

macOS launchd 専用。Linux systemd 対応は別 Issue で対応。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LAUNCHAGENTS_DIR: Path = Path.home() / "Library" / "LaunchAgents"

_PLIST_LABEL = "com.ghdag.runner"
_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{runner_dir}/run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{runner_dir}</string>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{runner_dir}/runner.log</string>
    <key>StandardErrorPath</key>
    <string>{runner_dir}/runner.err.log</string>
</dict>
</plist>
"""


def install_plist(runner_dir: Path, label: str) -> Path:
    """launchd plist を生成して ~/Library/LaunchAgents/ に配置する。

    Args:
        runner_dir: actions-runner が展開されたディレクトリ
        label: launchd ラベル（例: "com.ghdag.runner"）

    Returns:
        配置した plist ファイルのパス
    """
    LAUNCHAGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = LAUNCHAGENTS_DIR / f"{label}.plist"
    content = _PLIST_TEMPLATE.format(label=label, runner_dir=str(runner_dir))
    plist_path.write_text(content, encoding="utf-8")
    return plist_path


def uninstall_plist(plist_path: Path) -> None:
    """plist ファイルを削除する。存在しない場合は無視する。"""
    if plist_path.exists():
        plist_path.unlink()


def load(plist_path: Path) -> None:
    """launchctl load で runner を起動する。

    Raises:
        subprocess.CalledProcessError: launchctl が失敗した場合
    """
    subprocess.run(
        ["launchctl", "load", str(plist_path)],
        check=True,
    )


def unload(plist_path: Path) -> None:
    """launchctl unload で runner を停止する。

    Raises:
        subprocess.CalledProcessError: launchctl が失敗した場合
    """
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=True,
    )


def is_loaded(label: str) -> bool:
    """launchctl list で runner が起動中か確認する。"""
    result = subprocess.run(
        ["launchctl", "list", label],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
