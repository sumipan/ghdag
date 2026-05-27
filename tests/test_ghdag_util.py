"""Tests for ghdag.dag._util — tee path extraction (AC 5-1 ~ 5-5) and reader functions."""

import io
import threading
from unittest.mock import MagicMock

from ghdag.dag._util import _extract_tee_target, _stderr_reader, _stdout_reader


class TestExtractTeeTarget:
    def test_result_path_priority(self):
        """5-1: result_path が非 None ならコマンドの tee より優先"""
        result = _extract_tee_target("cat foo | tee out.md", result_path="explicit.md")
        assert result == "explicit.md"

    def test_non_md_extension(self):
        """5-2: .md 以外の拡張子も抽出できる"""
        result = _extract_tee_target("cat foo | tee result.json")
        assert result == "result.json"

    def test_quoted_path_with_space(self):
        """5-3: クォート付きパス（スペースあり）を抽出できる"""
        result = _extract_tee_target('cat foo | tee "path with space.md"')
        assert result == "path with space.md"

    def test_no_tee_no_result_path(self):
        """5-4: tee なし・result_path なし → None"""
        result = _extract_tee_target("echo hello", result_path=None)
        assert result is None

    def test_md_path_backward_compat(self):
        """5-5: 従来の .md パス（後方互換）"""
        result = _extract_tee_target("cat order.md | claude -p '...' | tee result.md", result_path=None)
        assert result == "result.md"

    def test_tee_with_minus_a_flag(self):
        """tee -a フラグ付きでもパスを抽出できる"""
        result = _extract_tee_target("cmd | tee -a output.txt")
        assert result == "output.txt"

    def test_result_path_none_falls_back_to_regex(self):
        """result_path=None のとき正規表現フォールバック"""
        result = _extract_tee_target("cmd | tee out.yaml", result_path=None)
        assert result == "out.yaml"


class TestStderrReader:
    """_stderr_reader の単体テスト"""

    def test_normal_read(self):
        """正常な EOF → buf に書き込まれスレッドが正常終了"""
        proc = MagicMock()
        proc.stderr.read.side_effect = [b"chunk1", b"chunk2", b""]
        buf = io.BytesIO()
        t = threading.Thread(target=_stderr_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert buf.getvalue() == b"chunk1chunk2"

    def test_oserror_on_read_exits_safely(self):
        """read() が OSError → スレッドがクラッシュせず安全に終了（例外を外に出さない）"""
        proc = MagicMock()
        proc.stderr.read.side_effect = OSError("broken pipe")
        proc.stderr.close.return_value = None
        buf = io.BytesIO()
        t = threading.Thread(target=_stderr_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()

    def test_valueerror_on_close_exits_safely(self):
        """close() が ValueError → スレッドがクラッシュせず安全に終了"""
        proc = MagicMock()
        proc.stderr.read.side_effect = [b"data", b""]
        proc.stderr.close.side_effect = ValueError("I/O operation on closed file")
        buf = io.BytesIO()
        t = threading.Thread(target=_stderr_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()


class TestStdoutReader:
    """_stdout_reader の単体テスト"""

    def test_normal_read(self):
        """正常な EOF → buf に書き込まれスレッドが正常終了"""
        proc = MagicMock()
        proc.stdout.read.side_effect = [b"out1", b"out2", b""]
        buf = io.BytesIO()
        t = threading.Thread(target=_stdout_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert buf.getvalue() == b"out1out2"

    def test_oserror_on_read_exits_safely(self):
        """read() が OSError → スレッドがクラッシュせず安全に終了"""
        proc = MagicMock()
        proc.stdout.read.side_effect = OSError("pipe broken")
        proc.stdout.close.return_value = None
        buf = io.BytesIO()
        t = threading.Thread(target=_stdout_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()

    def test_valueerror_on_close_exits_safely(self):
        """close() が ValueError → スレッドがクラッシュせず安全に終了"""
        proc = MagicMock()
        proc.stdout.read.side_effect = [b"data", b""]
        proc.stdout.close.side_effect = ValueError("I/O operation on closed file")
        buf = io.BytesIO()
        t = threading.Thread(target=_stdout_reader, args=(proc, buf))
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()
