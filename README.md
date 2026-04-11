# ghdag

A DAG-based workflow engine for GitHub issues and projects.

## Installation

```bash
pip install git+https://github.com/sumipan/ghdag.git
```

## Quick Start

Run all pending workflow steps once:

```bash
ghdag run workflow.yaml
```

Watch for changes and run continuously:

```bash
ghdag watch workflow.yaml
```

## Workflow YAML Example

```yaml
workflow:
  - trigger:
      label: "needs-triage"
    steps:
      - action: assign
        assignees: ["on-call-dev"]
      - action: label
        add: ["in-progress"]
        remove: ["needs-triage"]
      - action: comment
        body: "Assigned for triage. Will review shortly."
      - action: transition
        status: "In Progress"
```

## Architecture

ghdag is organized into three layers:

- **Layer 0 — Core/DSL**: DAG engine, state machine, and workflow schema parser. Pure Python, no GitHub dependency.
- **Layer 1 — GitHub Adapter**: Reads issues/projects from the GitHub API and maps them to Layer 0 state.
- **Layer 2 — CLI/Watcher**: Entry points (`ghdag run`, `ghdag watch`) that wire Layer 1 data into the Layer 0 engine.

## License

MIT
