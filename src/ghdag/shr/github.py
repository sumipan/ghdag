"""ghdag.shr.github — runner 登録トークン取得・ステータス確認。

gh CLI を subprocess 経由で呼び出す。認証は gh CLI の既存セッションを利用する。
"""

from __future__ import annotations

import json
import subprocess


def get_registration_token(repo: str) -> str:
    """runner 登録トークンを取得する。

    Args:
        repo: "owner/repo" 形式のリポジトリ名

    Returns:
        登録トークン文字列

    Raises:
        RuntimeError: gh api が失敗した場合
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runners/registration-token", "--method", "POST"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["token"]


def get_removal_token(repo: str) -> str:
    """runner 削除トークンを取得する。

    Args:
        repo: "owner/repo" 形式のリポジトリ名

    Returns:
        削除トークン文字列

    Raises:
        subprocess.CalledProcessError: gh api が失敗した場合
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runners/remove-token", "--method", "POST"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["token"]


def get_runner_status(repo: str, runner_name: str) -> str:
    """runner のオンライン/オフライン状態を取得する。

    Args:
        repo: "owner/repo" 形式のリポジトリ名
        runner_name: runner の名前

    Returns:
        "online" または "offline"

    Raises:
        RuntimeError: runner が見つからない、または API が失敗した場合
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/runners"],
        capture_output=True,
        text=True,
        check=True,
    )
    runners = json.loads(result.stdout)
    for runner in runners:
        if runner.get("name") == runner_name:
            return runner.get("status", "offline")
    return "offline"
