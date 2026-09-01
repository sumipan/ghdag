"""Tests for ghdag.io.done — done-marker I/O consolidation (nexus Issue #2675)."""

from __future__ import annotations

import concurrent.futures
import inspect
from pathlib import Path

import pytest


class TestIsDoneAndMarkDone:
    def test_mark_done_and_is_done(self, tmp_path: Path) -> None:
        from ghdag.io.done import is_done, mark_done

        mark_done(tmp_path, "uuid-x", 0)
        assert is_done(tmp_path, "uuid-x") is True

    def test_not_done(self, tmp_path: Path) -> None:
        from ghdag.io.done import is_done

        assert is_done(tmp_path, "uuid-y") is False

    def test_mark_done_writes_status(self, tmp_path: Path) -> None:
        from ghdag.io.done import mark_done

        mark_done(tmp_path, "u1", "REJECTED")
        assert (tmp_path / "u1").read_text() == "REJECTED"

    def test_mark_done_concurrent_no_empty_file(self, tmp_path: Path) -> None:
        from ghdag.io.done import mark_done

        def write_repeatedly(i: int) -> None:
            for _ in range(100):
                mark_done(tmp_path, "test-uuid", i)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_repeatedly, i) for i in range(10)]
            for f in futures:
                f.result()

        content = (tmp_path / "test-uuid").read_text()
        assert len(content) > 0
        assert int(content) in range(10)


class TestLoadDone:
    def test_load_done_nonexistent_dir(self, tmp_path: Path) -> None:
        from ghdag.io.done import load_done_from_dir

        assert load_done_from_dir(tmp_path / "nonexistent") == set()

    def test_load_done_includes_all(self, tmp_path: Path) -> None:
        from ghdag.io.done import load_done_from_dir, mark_done

        mark_done(tmp_path, "uuid-ok", 0)
        mark_done(tmp_path, "uuid-fail", 1)
        mark_done(tmp_path, "uuid-rejected", "REJECTED")

        assert load_done_from_dir(tmp_path) == {"uuid-ok", "uuid-fail", "uuid-rejected"}

    def test_load_succeeded_only_zero_and_empty(self, tmp_path: Path) -> None:
        from ghdag.io.done import load_succeeded_from_dir, mark_done

        mark_done(tmp_path, "uuid-ok", 0)
        mark_done(tmp_path, "uuid-rejected", "REJECTED")
        mark_done(tmp_path, "uuid-fail", 1)
        (tmp_path / "uuid-empty").write_text("")

        succeeded = load_succeeded_from_dir(tmp_path)
        assert "uuid-ok" in succeeded
        assert "uuid-empty" in succeeded
        assert "uuid-rejected" not in succeeded
        assert "uuid-fail" not in succeeded


class TestReadDoneContent:
    def test_returns_content(self, tmp_path: Path) -> None:
        from ghdag.io.done import read_done_content

        (tmp_path / "u1").write_text("0\n", encoding="utf-8")
        assert read_done_content(tmp_path, "u1") == "0\n"

    def test_missing_returns_none(self, tmp_path: Path) -> None:
        from ghdag.io.done import read_done_content

        assert read_done_content(tmp_path, "missing") is None

    def test_directory_returns_none(self, tmp_path: Path) -> None:
        from ghdag.io.done import read_done_content

        (tmp_path / "u1").mkdir()
        assert read_done_content(tmp_path, "u1") is None


class TestDepSucceeded:
    def test_success_zero(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        (tmp_path / "dep").write_text("0\n", encoding="utf-8")
        assert dep_succeeded(tmp_path, "dep") is True

    def test_success_empty(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        (tmp_path / "dep").write_text("", encoding="utf-8")
        assert dep_succeeded(tmp_path, "dep") is True

    def test_failure(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        (tmp_path / "dep").write_text("1\n", encoding="utf-8")
        assert dep_succeeded(tmp_path, "dep") is False

    def test_missing(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        assert dep_succeeded(tmp_path, "dep") is False

    def test_matches_interpret_done_success(self, tmp_path: Path) -> None:
        """io.done.dep_succeeded は pipeline.status.interpret_done の success 判定と一致する。"""
        from ghdag.io.done import dep_succeeded
        from ghdag.pipeline.status import interpret_done, read_done_content

        for raw in ("0\n", "", "00\n", "REJECTED\n", "1\n", "EMPTY_RESULT\n"):
            (tmp_path / "dep").write_text(raw, encoding="utf-8")
            expected = interpret_done(read_done_content(tmp_path, "dep")) == "success"
            assert dep_succeeded(tmp_path, "dep") is expected, repr(raw)


class TestShimCompat:
    def test_dag_state_reexports_same_objects(self) -> None:
        import ghdag.dag.state as dag_state
        import ghdag.io.done as io_done

        assert dag_state.is_done is io_done.is_done
        assert dag_state.mark_done is io_done.mark_done
        assert dag_state.load_done_from_dir is io_done.load_done_from_dir
        assert dag_state.load_succeeded_from_dir is io_done.load_succeeded_from_dir

    def test_pipeline_status_reexports_io_functions(self) -> None:
        import ghdag.io.done as io_done
        import ghdag.pipeline.status as pipeline_status

        assert pipeline_status.read_done_content is io_done.read_done_content
        assert pipeline_status.dep_succeeded is io_done.dep_succeeded

    def test_canonical_source_is_io_done(self) -> None:
        from ghdag.io.done import is_done, mark_done, read_done_content

        for fn in (is_done, mark_done, read_done_content):
            src = inspect.getsourcefile(fn)
            assert src is not None
            assert src.endswith("io/done.py") or src.endswith("io\\done.py")

    def test_maintenance_uses_io_done(self) -> None:
        import ghdag.maintenance as maint

        src = Path(inspect.getsourcefile(maint)).read_text(encoding="utf-8")
        assert "from ghdag.io.done import" in src
        assert "def _is_done(" not in src
        assert "def _mark_done(" not in src


class TestNoDuplicateDoneIo:
    def test_direct_done_io_only_in_io_done(self) -> None:
        """Acceptance: jobs/done の直接 I/O は io/done.py のみ。"""
        import ast
        import ghdag

        root = Path(inspect.getsourcefile(ghdag)).resolve().parent
        offenders: list[str] = []
        for path in root.rglob("*.py"):
            rel = path.relative_to(root)
            if rel.as_posix() == "io/done.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            text = path.read_text(encoding="utf-8")
            if "exec_done_dir" not in text and "jobs/done" not in text:
                continue
            # Look for open()/listdir/read_text/exists on done paths in modules
            # that still mention exec_done_dir/jobs/done outside imports.
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in {
                        "is_done",
                        "mark_done",
                        "load_done_from_dir",
                        "load_succeeded_from_dir",
                        "read_done_content",
                        "_is_done",
                        "_mark_done",
                    }:
                        # Local reimplementation of done I/O is forbidden outside io.done
                        if any(
                            isinstance(n, (ast.Call,))
                            and (
                                (isinstance(n.func, ast.Name) and n.func.id in {"open", "listdir"})
                                or (
                                    isinstance(n.func, ast.Attribute)
                                    and n.func.attr
                                    in {"open", "listdir", "exists", "makedirs", "read_text", "is_file"}
                                )
                            )
                            for n in ast.walk(node)
                        ):
                            offenders.append(f"{rel}:{node.name}")
        assert offenders == []
