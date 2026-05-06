"""Tests for ghdag.dag.parser — §5.2 acceptance criteria."""

import fcntl
import threading
import time
from pathlib import Path

import pytest

from ghdag.dag.parser import parse_exec_md, parse_jsonl, validate_dependencies
from ghdag.dag.models import Task


@pytest.fixture
def tmp_exec_md(tmp_path):
    """Helper to create a temporary exec.md with given content."""
    def _write(content: str) -> Path:
        p = tmp_path / "exec.md"
        p.write_text(content, encoding="utf-8")
        return p
    return _write


class TestParseExecMd:
    """§5.2 parser.py テストケース"""

    def test_normal_three_tasks(self, tmp_exec_md):
        """正常系: 3 つの Task をパースする"""
        content = (
            "uuid-a: cat order-a.md | claude -p \"...\" | tee result-a.md\n"
            "uuid-b[depends:uuid-a]: echo \"hello\" | tee result-b.md\n"
            "uuid-c[depends:uuid-a,uuid-b][retry:2]: some-command\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))

        assert len(tasks) == 3

        a = tasks[0]
        assert a.uuid == "uuid-a"
        assert a.depends == []
        assert a.retry == 0
        assert "cat order-a.md" in a.command

        b = tasks[1]
        assert b.uuid == "uuid-b"
        assert b.depends == ["uuid-a"]
        assert b.retry == 0

        c = tasks[2]
        assert c.uuid == "uuid-c"
        assert set(c.depends) == {"uuid-a", "uuid-b"}
        assert c.retry == 2

    def test_blank_and_comment_lines_skipped(self, tmp_exec_md):
        """空行・コメント: # comment 行と空行がスキップされること"""
        content = (
            "# comment line\n"
            "\n"
            "uuid-a: echo hello\n"
            "   \n"
            "# another comment\n"
            "uuid-b: echo world\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 2
        assert tasks[0].uuid == "uuid-a"
        assert tasks[1].uuid == "uuid-b"

    def test_invalid_lines_skipped(self, tmp_exec_md):
        """不正行: パース不能な行が例外を投げずスキップされること"""
        content = (
            "uuid-a: echo hello\n"
            "no-colon-line\n"
            "  just some random text  \n"
            "uuid-b: echo world\n"
        )
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 2
        assert tasks[0].uuid == "uuid-a"
        assert tasks[1].uuid == "uuid-b"

    def test_empty_file(self, tmp_exec_md):
        """空ファイル: 空の exec.md に対して空リスト [] を返すこと"""
        tasks = parse_exec_md(tmp_exec_md(""))
        assert tasks == []

    def test_file_not_found(self, tmp_path):
        """ファイル不存在: 存在しないパスに対して FileNotFoundError を送出すること"""
        with pytest.raises(FileNotFoundError):
            parse_exec_md(tmp_path / "nonexistent.md")

    def test_annotations_dict(self, tmp_exec_md):
        """Custom annotations beyond depends/retry are captured."""
        content = "uuid-a[depends:uuid-x][retry:1][model:sonnet]: echo hello\n"
        tasks = parse_exec_md(tmp_exec_md(content))
        assert len(tasks) == 1
        assert tasks[0].annotations == {"model": "sonnet"}


class TestParseJsonl:
    """JSONL パーサのテストケース（P1〜P9）"""

    def test_p1_normal_line(self):
        """P1: 正常な JSONL 行をパースできる"""
        text = '{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"
        assert tasks[0].command == "echo hi"
        assert tasks[0].depends == []
        assert tasks[0].result_path is None
        assert tasks[0].idempotency_key is None

    def test_p2_result_path(self):
        """P2: result_path を読み取れる"""
        text = '{"uuid":"a","command":"echo hi","depends":[],"result_path":"out.md"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].result_path == "out.md"

    def test_p3_idempotency_key(self):
        """P3: idempotency_key を読み取れる"""
        text = '{"uuid":"a","command":"echo hi","depends":[],"idempotency_key":"iss:b:1"}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].idempotency_key == "iss:b:1"

    def test_p4_invalid_json_skipped(self):
        """P4: 不正な JSON 行をスキップ"""
        text = 'not json\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p5_empty_lines_skipped(self):
        """P5: 空行をスキップ"""
        text = '\n\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p6_missing_uuid_skipped(self):
        """P6: 必須フィールド（uuid）欠落行をスキップ"""
        text = '{"command":"echo hi","depends":[]}\n{"uuid":"a","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"

    def test_p7_missing_command_skipped(self):
        """P7: 必須フィールド（command）欠落行をスキップ"""
        text = '{"uuid":"a","depends":[]}\n{"uuid":"b","command":"echo hi","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "b"

    def test_p8_duplicate_uuid_last_wins(self):
        """P8: 同一 uuid の重複は後勝ち"""
        text = '{"uuid":"a","command":"first","depends":[]}\n{"uuid":"a","command":"second","depends":[]}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].uuid == "a"
        assert tasks[0].command == "second"

    def test_p9_retry_and_annotations(self):
        """P9: retry / annotations を読み取れる"""
        text = '{"uuid":"a","command":"echo","depends":[],"retry":2,"annotations":{"model":"sonnet"}}\n'
        tasks = parse_jsonl(text)
        assert len(tasks) == 1
        assert tasks[0].retry == 2
        assert tasks[0].annotations == {"model": "sonnet"}


class TestParseExecMdLocking:
    """AC 2-1, 2-2: parse_exec_md の共有ロック"""

    def test_exclusive_lock_blocks_read(self, tmp_exec_md):
        """2-1: LOCK_EX を持つスレッドが完了するまで parse_exec_md がブロックされる"""
        path = tmp_exec_md("uuid-a: echo hello\n")

        lock_acquired = threading.Event()
        lock_released_at = [None]
        reader_done_at = [None]

        def exclusive_holder():
            with open(str(path)) as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                lock_acquired.set()
                time.sleep(0.4)
                lock_released_at[0] = time.monotonic()
                fcntl.flock(f, fcntl.LOCK_UN)

        def shared_reader():
            lock_acquired.wait(timeout=2.0)
            parse_exec_md(path)
            reader_done_at[0] = time.monotonic()

        t1 = threading.Thread(target=exclusive_holder)
        t2 = threading.Thread(target=shared_reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert lock_released_at[0] is not None
        assert reader_done_at[0] is not None
        assert reader_done_at[0] >= lock_released_at[0] - 0.05  # reader completed after lock released

    def test_concurrent_reads_do_not_block(self, tmp_exec_md):
        """2-2: 複数 parse_exec_md は並行して実行できる（LOCK_SH は競合しない）"""
        path = tmp_exec_md("uuid-a: echo hello\n")
        errors = []
        results = []

        def reader():
            try:
                tasks = parse_exec_md(path)
                results.append(len(tasks))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 5
        assert all(r == 1 for r in results)


class TestValidateDependencies:
    """AC 3-1 ~ 3-5: validate_dependencies"""

    def _make_task(self, uuid: str, depends: list[str]) -> Task:
        return Task(uuid=uuid, command=f"echo {uuid}", depends=depends)

    def test_orphan_dep_detected(self):
        """3-1: 孤立依存（exec.md にも done にも存在しない）を検出する"""
        tasks = [self._make_task("uuid-b", ["uuid-x"])]
        result = validate_dependencies(tasks, done=set())
        assert result == {"uuid-b": "orphan_dep:uuid-x"}

    def test_mutual_cycle_detected(self):
        """3-2: 相互依存の閉路を検出する"""
        tasks = [
            self._make_task("uuid-a", ["uuid-b"]),
            self._make_task("uuid-b", ["uuid-a"]),
        ]
        result = validate_dependencies(tasks, done=set())
        assert result.get("uuid-a") == "cycle"
        assert result.get("uuid-b") == "cycle"

    def test_done_dep_is_not_orphan(self):
        """3-3: exec-done に存在する依存は孤立でない"""
        tasks = [self._make_task("uuid-b", ["uuid-a"])]
        result = validate_dependencies(tasks, done={"uuid-a"})
        assert result == {}

    def test_normal_linear_graph(self):
        """3-4: 正常な線形依存グラフは空辞書を返す"""
        tasks = [
            self._make_task("uuid-a", []),
            self._make_task("uuid-b", ["uuid-a"]),
        ]
        result = validate_dependencies(tasks, done=set())
        assert result == {}

    def test_self_reference_detected(self):
        """3-5: 自己参照（自己ループ）を閉路として検出する"""
        tasks = [self._make_task("uuid-a", ["uuid-a"])]
        result = validate_dependencies(tasks, done=set())
        assert result.get("uuid-a") == "cycle"
