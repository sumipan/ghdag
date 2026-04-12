"""ghdag.shr.runner — actions-runner バイナリの DL・展開・config.sh 実行・登録解除。

外部コマンド実行は subprocess.run を直接使用する。
"""

from __future__ import annotations

import platform
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# actions-runner の GitHub リリースベース URL
_RUNNER_RELEASES_URL = "https://github.com/actions/runner/releases/download"
# デフォルトバージョン（gh api で最新取得は将来対応）
_DEFAULT_VERSION = "2.321.0"


def _get_runner_archive_url(version: str = _DEFAULT_VERSION) -> str:
    """プラットフォームに合わせた actions-runner アーカイブ URL を返す。"""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        arch = "arm64" if machine == "arm64" else "x64"
        filename = f"actions-runner-osx-{arch}-{version}.tar.gz"
    else:
        arch = "arm64" if "arm" in machine or "aarch" in machine else "x64"
        filename = f"actions-runner-linux-{arch}-{version}.tar.gz"

    return f"{_RUNNER_RELEASES_URL}/v{version}/{filename}"


def download_runner(dest: Path) -> Path:
    """actions-runner の最新リリースをダウンロード・展開する。

    Args:
        dest: 展開先ディレクトリ（存在しない場合は作成する）

    Returns:
        展開先ディレクトリ（dest と同じ）

    Raises:
        RuntimeError: ダウンロードに失敗した場合
    """
    dest.mkdir(parents=True, exist_ok=True)
    url = _get_runner_archive_url()

    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            urllib.request.urlretrieve(url, tmp_path)

        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(dest)

        tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        raise RuntimeError(f"actions-runner のダウンロードに失敗しました: {exc}") from exc

    return dest


def configure_runner(runner_dir: Path, repo: str, token: str, labels: list[str]) -> None:
    """config.sh を実行して runner を GitHub に登録する。

    Args:
        runner_dir: actions-runner 展開ディレクトリ
        repo: "owner/repo" 形式
        token: 登録トークン
        labels: runner ラベルのリスト

    Raises:
        subprocess.CalledProcessError: config.sh が失敗した場合
    """
    config_sh = runner_dir / "config.sh"
    labels_str = ",".join(labels)
    subprocess.run(
        [
            str(config_sh),
            "--url", f"https://github.com/{repo}",
            "--token", token,
            "--labels", labels_str,
            "--unattended",
        ],
        cwd=runner_dir,
        check=True,
    )


def remove_runner(runner_dir: Path, token: str) -> None:
    """config.sh remove で runner の GitHub 登録を解除する。

    Args:
        runner_dir: actions-runner 展開ディレクトリ
        token: 削除トークン

    Raises:
        subprocess.CalledProcessError: config.sh remove が失敗した場合
    """
    config_sh = runner_dir / "config.sh"
    subprocess.run(
        [str(config_sh), "remove", "--token", token],
        cwd=runner_dir,
        check=True,
    )
