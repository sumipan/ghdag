# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.10.2] - 2026-04-22

### Fixed

- `ghdag shr init` wrote a fully-resolved absolute path (e.g. `/Users/alice/.ghdag/runner/run.sh`) into Procfile, breaking the entry whenever the repository was synced to a host with a different home directory. The Procfile entry now uses `$HOME/...` when the runner directory lives under `$HOME`.

## [0.10.1] - 2026-04-22

### Fixed

- Wheel was missing `ghdag/ui/static/index.html`, causing `ghdag ui` to crash with `FileNotFoundError` on first request. Added `[tool.setuptools.package-data]` so static assets are bundled.

## [0.8.0] - 2026-04-18

### Added

- `template_dir` setting in workflow YAML to configure the template directory per workflow (#14)
- Relative `template_dir` paths are resolved relative to the workflow definition file's directory
- Falls back to `"templates"` when `template_dir` is not specified (backward compatible)

## [0.7.2] - 2026-04-17

### Fixed

- `DagEngine._launch_task()` now passes `cwd` to `subprocess.Popen`, preventing task failures when the parent process's working directory differs from the repository root

### Added

- `DagConfig.cwd` field — explicit working directory for task subprocesses (defaults to `None`, inheriting parent cwd for backward compatibility)
- CLI `ghdag run` auto-derives `cwd` from `exec_md` path (parent of `queue/`)

## [0.5.0] - 2026-04-13

### Added

- `ghdag shr` subcommand for self-hosted runner management (`init`, `status`, `remove`)
- Daemon management via overmind integration (migrated from launchd)
- `ghdag watch --once` flag for single-shot event-driven execution via GitHub Actions webhooks

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
