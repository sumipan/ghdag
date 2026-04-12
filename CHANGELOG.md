# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.0] - 2026-04-12

### Added

- Template context now includes `result_filename` and `${dep_id}_result_filename` for referencing pipeline step outputs (#2, PR #4)
- `context_hook` support in workflow YAML for injecting custom context via external scripts (#3, PR #5)

## [0.3.0] - 2026-04-11

### Added

- Layer 2: CLI (`ghdag run`, `ghdag watch`) and watcher module migrated from diary repo
- Extended workflow schema: multi-step DAG, `--model` flag, issue context injection, backward guard, reset handler
- Trigger entry validation for `label` and `handler` fields in the loader
- `--hooks` option for `ghdag run` to inject DagHooks from external modules

### Changed

- `requires-python` set to `>=3.11`

### Fixed

- `TemplateOrderBuilder` now uses `safe_substitute()` to avoid `KeyError` on template variables intended as AI instructions (e.g. `$base_branch`)
- `_load_hooks()` inserts `cwd` into `sys.path` so `--hooks scripts.diary_hooks` resolves correctly

## [0.2.0] - 2026-03-01

### Added

- Layer 1: GitHub adapter (pipeline module) for reading issues and projects via the GitHub API
- State mapping between GitHub issue/project data and Layer 0 engine state

## [0.1.0] - 2026-02-01

### Added

- Initial package setup with DAG engine extracted from diary repo
- Layer 0: Core DAG engine, state machine, and workflow schema parser
- `pyproject.toml` with `setuptools` build backend and `pytest` dev dependency
