"""Tests for ghdag shr subcommand (Issue #88).

Covers AC-1 through AC-6 from the design document.
All external calls (subprocess.run, file I/O to home dir) are mocked.
Updated: launchd → overmind (Procfile) ベース.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.cli import main


# ---------------------------------------------------------------------------
# AC-1: ghdag shr init
# ---------------------------------------------------------------------------

class TestShrInit:
    """AC-1: ghdag shr init --repo owner/repo --labels ghdag"""

    def test_init_normal(self, tmp_path):
        """AC-1-1: 正常系 — runner DL、config.sh、Procfile エントリ追加、shr.json 保存"""
        procfile = tmp_path / "Procfile"
        procfile.write_text("# existing\ndag_runner: echo hi\n")

        with (
            patch("ghdag.shr.github.get_registration_token", return_value="tok123"),
            patch("ghdag.shr.runner.download_runner") as mock_dl,
            patch("ghdag.shr.runner.configure_runner") as mock_cfg,
            patch("ghdag.shr.daemon.PROCFILE_PATH", procfile),
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
            patch("ghdag.shr.config.RUNNER_DIR", tmp_path / "runner"),
        ):
            mock_dl.return_value = tmp_path / "runner"
            main(["shr", "init", "--repo", "owner/repo", "--labels", "ghdag"])

        mock_dl.assert_called_once()
        mock_cfg.assert_called_once()
        assert (tmp_path / "shr.json").exists()
        data = json.loads((tmp_path / "shr.json").read_text())
        assert data["repo"] == "owner/repo"
        assert "ghdag" in data["labels"]
        assert data["process_name"] == "shr_runner"
        # Procfile にエントリが追加されていること
        content = procfile.read_text()
        assert "shr_runner:" in content

    def test_init_missing_repo(self, capsys):
        """AC-1-2: --repo なし → argparse がエラー exit code 2"""
        with pytest.raises(SystemExit) as exc:
            main(["shr", "init", "--labels", "ghdag"])
        assert exc.value.code == 2

    def test_init_missing_labels(self, capsys):
        """AC-1-3: --labels なし → argparse がエラー exit code 2"""
        with pytest.raises(SystemExit) as exc:
            main(["shr", "init", "--repo", "owner/repo"])
        assert exc.value.code == 2

    def test_init_token_failure(self, tmp_path, capsys):
        """AC-1-4: gh api が失敗 → stderr にエラー、exit code 1、ファイル残さない"""
        with (
            patch("ghdag.shr.github.get_registration_token", side_effect=RuntimeError("HTTP 403")),
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "init", "--repo", "owner/repo", "--labels", "ghdag"])
        assert exc.value.code == 1
        assert not (tmp_path / "shr.json").exists()
        captured = capsys.readouterr()
        assert captured.err  # stderr に何か出力されている

    def test_init_runner_download_failure(self, tmp_path, capsys):
        """AC-1-5: runner DL 失敗 → stderr にエラー、exit code 1"""
        with (
            patch("ghdag.shr.github.get_registration_token", return_value="tok123"),
            patch("ghdag.shr.runner.download_runner", side_effect=RuntimeError("404")),
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
            patch("ghdag.shr.config.RUNNER_DIR", tmp_path / "runner"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "init", "--repo", "owner/repo", "--labels", "ghdag"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.err

    def test_init_already_initialized(self, tmp_path, capsys):
        """AC-1-6: shr.json が既に存在 → エラーメッセージ、exit code 1"""
        config_path = tmp_path / "shr.json"
        config_path.write_text('{"repo": "existing/repo"}')
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "init", "--repo", "owner/repo", "--labels", "ghdag"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "teardown" in captured.err or "初期化済" in captured.err


# ---------------------------------------------------------------------------
# AC-2: ghdag shr start
# ---------------------------------------------------------------------------

class TestShrStart:
    """AC-2: ghdag shr start"""

    def _make_config(self, tmp_path) -> Path:
        config_path = tmp_path / "shr.json"
        config_path.write_text(json.dumps({
            "repo": "owner/repo",
            "labels": ["ghdag"],
            "runner_dir": str(tmp_path / "runner"),
            "process_name": "shr_runner",
        }))
        return config_path

    def test_start_normal(self, tmp_path):
        """AC-2-1: 正常系 — overmind restart が実行される、exit code 0"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=False),
            patch("ghdag.shr.daemon.start") as mock_start,
        ):
            main(["shr", "start"])
        mock_start.assert_called_once_with("shr_runner")

    def test_start_not_initialized(self, tmp_path, capsys):
        """AC-2-2: shr.json なし → エラー、exit code 1"""
        with (
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "start"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "init" in captured.err

    def test_start_already_running(self, tmp_path, capsys):
        """AC-2-3: 既に起動中 → メッセージ表示、exit code 0"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=True),
            patch("ghdag.shr.daemon.start") as mock_start,
        ):
            main(["shr", "start"])
        mock_start.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out or captured.err  # 何らかのメッセージ


# ---------------------------------------------------------------------------
# AC-3: ghdag shr stop
# ---------------------------------------------------------------------------

class TestShrStop:
    """AC-3: ghdag shr stop"""

    def _make_config(self, tmp_path) -> Path:
        config_path = tmp_path / "shr.json"
        config_path.write_text(json.dumps({
            "repo": "owner/repo",
            "labels": ["ghdag"],
            "runner_dir": str(tmp_path / "runner"),
            "process_name": "shr_runner",
        }))
        return config_path

    def test_stop_normal(self, tmp_path):
        """AC-3-1: 正常系 — overmind stop が実行される、exit code 0"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=True),
            patch("ghdag.shr.daemon.stop") as mock_stop,
        ):
            main(["shr", "stop"])
        mock_stop.assert_called_once_with("shr_runner")

    def test_stop_already_stopped(self, tmp_path, capsys):
        """AC-3-2: 既に停止中 → メッセージ表示、exit code 0"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=False),
            patch("ghdag.shr.daemon.stop") as mock_stop,
        ):
            main(["shr", "stop"])
        mock_stop.assert_not_called()
        captured = capsys.readouterr()
        assert captured.out or captured.err

    def test_stop_not_initialized(self, tmp_path, capsys):
        """AC-3-3: shr.json なし → エラー、exit code 1"""
        with (
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "stop"])
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "init" in captured.err


# ---------------------------------------------------------------------------
# AC-4: ghdag shr status
# ---------------------------------------------------------------------------

class TestShrStatus:
    """AC-4: ghdag shr status"""

    def _make_config(self, tmp_path) -> Path:
        config_path = tmp_path / "shr.json"
        config_path.write_text(json.dumps({
            "repo": "owner/repo",
            "labels": ["ghdag"],
            "runner_dir": str(tmp_path / "runner"),
            "process_name": "shr_runner",
        }))
        return config_path

    def test_status_running_online(self, tmp_path, capsys):
        """AC-4-1: 起動中 + オンライン → 両方表示"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=True),
            patch("ghdag.shr.github.get_runner_status", return_value="online"),
        ):
            main(["shr", "status"])
        captured = capsys.readouterr()
        assert "起動中" in captured.out or "running" in captured.out.lower()
        assert "online" in captured.out

    def test_status_stopped_offline(self, tmp_path, capsys):
        """AC-4-2: 停止中 + オフライン → 両方表示"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=False),
            patch("ghdag.shr.github.get_runner_status", return_value="offline"),
        ):
            main(["shr", "status"])
        captured = capsys.readouterr()
        assert "停止中" in captured.out or "stopped" in captured.out.lower()
        assert "offline" in captured.out

    def test_status_not_initialized(self, tmp_path, capsys):
        """AC-4-3: shr.json なし → エラー、exit code 1"""
        with (
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "status"])
        assert exc.value.code == 1

    def test_status_github_api_failure(self, tmp_path, capsys):
        """AC-4-4: GitHub API 失敗 → ローカル状態は表示、GitHub は「確認失敗」、exit code 0"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=True),
            patch("ghdag.shr.github.get_runner_status", side_effect=RuntimeError("API error")),
        ):
            main(["shr", "status"])  # exit code 0 → SystemExit が起きないこと
        captured = capsys.readouterr()
        assert "起動中" in captured.out or "running" in captured.out.lower()
        assert "確認失敗" in captured.out or "error" in captured.out.lower() or "failed" in captured.out.lower()


# ---------------------------------------------------------------------------
# AC-5: ghdag shr teardown
# ---------------------------------------------------------------------------

class TestShrTeardown:
    """AC-5: ghdag shr teardown"""

    def _make_config(self, tmp_path) -> Path:
        config_path = tmp_path / "shr.json"
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        config_path.write_text(json.dumps({
            "repo": "owner/repo",
            "labels": ["ghdag"],
            "runner_dir": str(runner_dir),
            "process_name": "shr_runner",
        }))
        return config_path

    def test_teardown_normal(self, tmp_path):
        """AC-5-1: 正常系 — remove、Procfile エントリ削除、runner 削除、shr.json 削除"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=False),
            patch("ghdag.shr.github.get_removal_token", return_value="rmtok"),
            patch("ghdag.shr.runner.remove_runner") as mock_remove,
            patch("ghdag.shr.daemon.uninstall_procfile_entry") as mock_uninstall,
        ):
            main(["shr", "teardown"])
        mock_remove.assert_called_once()
        mock_uninstall.assert_called_once_with("shr_runner")
        assert not config_path.exists()

    def test_teardown_runner_running(self, tmp_path):
        """AC-5-2: runner 起動中 → 先に stop してから teardown"""
        config_path = self._make_config(tmp_path)
        call_order = []
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=True),
            patch("ghdag.shr.daemon.stop", side_effect=lambda p: call_order.append("stop")),
            patch("ghdag.shr.github.get_removal_token", return_value="rmtok"),
            patch("ghdag.shr.runner.remove_runner", side_effect=lambda d, t: call_order.append("remove")),
            patch("ghdag.shr.daemon.uninstall_procfile_entry"),
        ):
            main(["shr", "teardown"])
        assert call_order.index("stop") < call_order.index("remove")

    def test_teardown_not_initialized(self, tmp_path, capsys):
        """AC-5-3: shr.json なし → エラー、exit code 1"""
        with (
            patch("ghdag.shr.config.CONFIG_PATH", tmp_path / "shr.json"),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "teardown"])
        assert exc.value.code == 1

    def test_teardown_remove_token_failure(self, tmp_path, capsys):
        """AC-5-4: remove トークン取得失敗 → エラー、exit code 1、ファイル残す"""
        config_path = self._make_config(tmp_path)
        with (
            patch("ghdag.shr.config.CONFIG_PATH", config_path),
            patch("ghdag.shr.daemon.is_running", return_value=False),
            patch("ghdag.shr.github.get_removal_token", side_effect=RuntimeError("API fail")),
        ):
            with pytest.raises(SystemExit) as exc:
                main(["shr", "teardown"])
        assert exc.value.code == 1
        assert config_path.exists()  # ローカルファイルは残す


# ---------------------------------------------------------------------------
# AC-6: 既存機能への影響なし
# ---------------------------------------------------------------------------

class TestExistingCommandsUnaffected:
    """AC-6: 既存サブコマンドが影響を受けないこと"""

    def test_version_still_works(self, capsys):
        """AC-6-2: ghdag version が動作する"""
        main(["version"])
        captured = capsys.readouterr()
        assert captured.out.strip()  # バージョン文字列が出力される

    def test_shr_help_does_not_break_parser(self, capsys):
        """shr サブコマンドグループが parser に存在する"""
        with pytest.raises(SystemExit) as exc:
            main(["shr", "--help"])
        assert exc.value.code == 0

    def test_shr_init_help(self, capsys):
        """shr init --help が動作する"""
        with pytest.raises(SystemExit) as exc:
            main(["shr", "init", "--help"])
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Unit tests for shr modules
# ---------------------------------------------------------------------------

class TestShrConfig:
    """ghdag.shr.config の単体テスト"""

    def test_save_and_load_config(self, tmp_path):
        from ghdag.shr.config import ShrConfig, save_config, load_config
        import ghdag.shr.config as cfg_module

        cfg = ShrConfig(
            repo="owner/repo",
            labels=["ghdag"],
            runner_dir=str(tmp_path / "runner"),
            process_name="shr_runner",
        )
        with patch.object(cfg_module, "CONFIG_PATH", tmp_path / "shr.json"):
            save_config(cfg)
            loaded = load_config()

        assert loaded.repo == "owner/repo"
        assert loaded.labels == ["ghdag"]
        assert loaded.process_name == "shr_runner"

    def test_load_config_missing(self, tmp_path):
        """CONFIG_PATH が存在しない場合 FileNotFoundError"""
        from ghdag.shr.config import load_config
        import ghdag.shr.config as cfg_module

        with patch.object(cfg_module, "CONFIG_PATH", tmp_path / "missing.json"):
            with pytest.raises(FileNotFoundError):
                load_config()


class TestShrGithub:
    """ghdag.shr.github の単体テスト"""

    def test_get_registration_token(self):
        from ghdag.shr.github import get_registration_token
        result_json = json.dumps({"token": "abc123"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=result_json, returncode=0)
            token = get_registration_token("owner/repo")
        assert token == "abc123"

    def test_get_registration_token_failure(self):
        from ghdag.shr.github import get_registration_token
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "gh")
            with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
                get_registration_token("owner/repo")

    def test_get_removal_token(self):
        from ghdag.shr.github import get_removal_token
        result_json = json.dumps({"token": "rmtok456"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=result_json, returncode=0)
            token = get_removal_token("owner/repo")
        assert token == "rmtok456"

    def test_get_runner_status(self):
        from ghdag.shr.github import get_runner_status
        runners_json = json.dumps([{"name": "myrunner", "status": "online"}])
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=runners_json, returncode=0)
            status = get_runner_status("owner/repo", "myrunner")
        assert status == "online"


class TestShrDaemon:
    """ghdag.shr.daemon の単体テスト（overmind ベース）"""

    def test_install_procfile_entry(self, tmp_path):
        from ghdag.shr.daemon import install_procfile_entry
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        procfile = tmp_path / "Procfile"
        procfile.write_text("dag_runner: echo hi\n")

        with patch("ghdag.shr.daemon.PROCFILE_PATH", procfile):
            result = install_procfile_entry(runner_dir, "shr_runner")
        assert result == "shr_runner"
        content = procfile.read_text()
        assert "shr_runner:" in content
        assert "run.sh" in content

    def test_install_procfile_entry_uses_home_relative_path(self, tmp_path, monkeypatch):
        """`runner_dir` が $HOME 配下なら Procfile には $HOME/... を書き込む。"""
        from ghdag.shr.daemon import install_procfile_entry

        fake_home = tmp_path / "home"
        runner_dir = fake_home / ".ghdag" / "runner"
        runner_dir.mkdir(parents=True)
        procfile = tmp_path / "Procfile"
        procfile.write_text("")

        monkeypatch.setenv("HOME", str(fake_home))
        with patch("ghdag.shr.daemon.PROCFILE_PATH", procfile), \
                patch("ghdag.shr.daemon.Path.home", return_value=fake_home):
            install_procfile_entry(runner_dir, "shr_runner")

        content = procfile.read_text()
        assert "$HOME/.ghdag/runner/run.sh" in content
        assert str(runner_dir) not in content

    def test_install_procfile_entry_outside_home_keeps_absolute(self, tmp_path, monkeypatch):
        """$HOME 配下でない `runner_dir` は絶対パスのまま書く。"""
        from ghdag.shr.daemon import install_procfile_entry

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        runner_dir = tmp_path / "elsewhere" / "runner"
        runner_dir.mkdir(parents=True)
        procfile = tmp_path / "Procfile"
        procfile.write_text("")

        monkeypatch.setenv("HOME", str(fake_home))
        with patch("ghdag.shr.daemon.PROCFILE_PATH", procfile), \
                patch("ghdag.shr.daemon.Path.home", return_value=fake_home):
            install_procfile_entry(runner_dir, "shr_runner")

        content = procfile.read_text()
        assert str(runner_dir) in content
        assert "$HOME" not in content

    def test_install_procfile_entry_duplicate(self, tmp_path):
        from ghdag.shr.daemon import install_procfile_entry
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        procfile = tmp_path / "Procfile"
        procfile.write_text("shr_runner: echo existing\n")

        with patch("ghdag.shr.daemon.PROCFILE_PATH", procfile):
            with pytest.raises(RuntimeError, match="既に"):
                install_procfile_entry(runner_dir, "shr_runner")

    def test_uninstall_procfile_entry(self, tmp_path):
        from ghdag.shr.daemon import uninstall_procfile_entry
        procfile = tmp_path / "Procfile"
        procfile.write_text(
            "dag_runner: echo hi\n"
            "\n# Self-hosted runner (ghdag shr)\n"
            "shr_runner: /path/to/run.sh\n"
        )

        with patch("ghdag.shr.daemon.PROCFILE_PATH", procfile):
            uninstall_procfile_entry("shr_runner")
        content = procfile.read_text()
        assert "shr_runner" not in content
        assert "dag_runner" in content

    def test_is_running_true(self):
        from ghdag.shr.daemon import is_running
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="shr_runner    running    pid: 12345\n",
                returncode=0,
            )
            result = is_running("shr_runner")
        assert result is True

    def test_is_running_false(self):
        from ghdag.shr.daemon import is_running
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="shr_runner    stopped\n",
                returncode=0,
            )
            result = is_running("shr_runner")
        assert result is False

    def test_start_calls_overmind(self):
        from ghdag.shr.daemon import start
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            start("shr_runner")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "overmind" in cmd
        assert "restart" in cmd
        assert "shr_runner" in cmd

    def test_stop_calls_overmind(self):
        from ghdag.shr.daemon import stop
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            stop("shr_runner")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "overmind" in cmd
        assert "stop" in cmd
        assert "shr_runner" in cmd


class TestShrRunner:
    """ghdag.shr.runner の単体テスト"""

    def test_configure_runner_calls_config_sh(self, tmp_path):
        from ghdag.shr.runner import configure_runner
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        # config.sh を dummy として作成
        config_sh = runner_dir / "config.sh"
        config_sh.write_text("#!/bin/sh\necho configured")
        config_sh.chmod(0o755)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            configure_runner(runner_dir, "owner/repo", "tok123", ["ghdag"])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--unattended" in cmd

    def test_remove_runner_calls_config_sh_remove(self, tmp_path):
        from ghdag.shr.runner import remove_runner
        runner_dir = tmp_path / "runner"
        runner_dir.mkdir()
        config_sh = runner_dir / "config.sh"
        config_sh.write_text("#!/bin/sh\necho removed")
        config_sh.chmod(0o755)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            remove_runner(runner_dir, "rmtok")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "remove" in cmd
