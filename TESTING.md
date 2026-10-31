# Testing policy

Tests protect **contracts**: given inputs, expected outputs, and side effects at process boundaries. They must not pin function names, private helpers, call order, source text, or documentation wording.

## Principles

1. **Pin behavior, not implementation.** Assert on observable results and side effects, not on how the code is written.
2. **One fact, one place.** Do not copy values from source, config, or YAML into tests. Docs are guides, not ground truth—do not assert exact doc phrases.
3. **Names describe behavior; history lives in docstrings.** Put issue references in docstrings, not in file or test names.
4. **Fake only at boundaries.** Mock or stub LLM CLIs, subprocesses, HTTP, clocks, and filesystem I/O—not internal helpers in the same package.
5. **Repo-wide invariants belong in one conventions test.** Put cross-cutting rules in `tests/conventions/` instead of repeating scans per domain.
6. **Tests are liabilities.** When behavior is removed, delete its tests in the same PR. Do not keep permanent “removed” or migration checks—review once, then drop.
7. **Keep volume bounded.** Prefer one test per contract. Keep new test files under 800 lines and total test size proportional to source.

## Rules

| ID | Rule |
|----|------|
| T1 | Do not pin implementation strings (`inspect.getsource`, reading `.py` for substrings, `ast.parse` on source, `__code__.co_consts`). |
| T2 | Do not pin documentation wording by reading `README.md`, `docs/`, or templates and asserting specific phrases exist. Template checks may validate render output or bash syntax instead. |
| T3 | Do not permanently test deletion or absence (`test_*_removed`, `"x" not in source`, `not hasattr`, import errors for removed modules). |
| T4 | Do not name files or tests after issue numbers, acceptance-criterion IDs, or phase names. |
| T5 | Do not pin internal call sequences with `patch` and `assert_called*` on same-package helpers; assert outcomes instead. |
| T6 | Do not ratchet structural counts (step counts, template lists) unless the public `__all__` export list is the contract under test. |
| T7 | Do not bake host-repo vocabulary (Slack, `jobs/`, workflow label names from downstream consumers) into tests as fixed strings. |
| T8 | Keep every test file under 1,000 lines; new files should stay under 800 lines. |
| T9 | Do not add characterization tests that lock pre-change behavior; for existing features, rely on the P1 tests baseline gate instead of AC-level “no regression” tests. |
| T12 | Treat `--cov-fail-under=70` as frozen at 70; do not add tests whose only purpose is to satisfy coverage. |

## Removing tests

For each candidate file flagged by policy review:

1. State in one line **which contract** the test protects. If you cannot, delete it.
2. Check whether another behavior test already protects that contract. If yes, delete the duplicate.
3. If the contract no longer matters (removal check, migration check, wording check), delete the test.
4. If the contract still matters, rewrite to a single behavior test that does not violate T1–T6 and is not longer than the old test. If rewrite would grow the file, delete instead.

From a nexus checkout, inventory candidates with:

```bash
python3 scripts/test-policy-audit.py --repo <path-to-this-repo>
```
