"""loader が label_namespace / transitions / reset_label / roles / step.role を読む (Issue #3029)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ghdag.workflow.loader import _parse, load_workflows
from ghdag.workflow.state_machine import _load_workflow_config

# worktree: .../nexus/.claude/external/ghdag/worktrees/<id>/tests/workflow/this_file
# parents[7] == nexus リポジトリルート（CLAUDE.md §10: 実ファイルの形を使う）
_NEXUS_ISSUESMITH_YML = (
    Path(__file__).resolve().parents[7] / "workflows" / "issuesmith.yml"
)


def _stub_templates(workflow_dir: Path, yml_text: str) -> None:
    """issuesmith.yml が参照する template ファイルを空スタブで用意する。"""
    data = yaml.safe_load(yml_text)
    template_dir_name = data.get("template_dir") or "templates"
    tdir = workflow_dir / template_dir_name
    tdir.mkdir(parents=True, exist_ok=True)
    for name in sorted(set(re.findall(r"^\s+template:\s*(\S+)", yml_text, re.M))):
        (tdir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")


def _issuesmith_fixture(tmp_path: Path, *, inject_roles: bool = False) -> Path:
    """実 issuesmith.yml を tmp にコピーし、必要なら roles / step.role を注入する。"""
    assert _NEXUS_ISSUESMITH_YML.is_file(), f"missing fixture: {_NEXUS_ISSUESMITH_YML}"
    text = _NEXUS_ISSUESMITH_YML.read_text(encoding="utf-8")
    if inject_roles:
        data = yaml.safe_load(text)
        data["roles"] = {"design": ["claude", "codex"], "impl": ["cursor", "claude"]}
        # 先頭ハンドラの先頭ステップに role を付与（実ファイル形を維持）
        first_handler = next(iter(data["handlers"].values()))
        first_handler["steps"][0]["role"] = "design"
        text = yaml.dump(data, allow_unicode=True, sort_keys=False)
    dest = tmp_path / "issuesmith.yml"
    dest.write_text(text, encoding="utf-8")
    _stub_templates(tmp_path, text)
    return tmp_path


class TestLoaderReadsWorkflowFields:
    def test_load_issuesmith_label_namespace_transitions_reset(self, tmp_path: Path) -> None:
        wf_dir = _issuesmith_fixture(tmp_path)
        configs = load_workflows(wf_dir)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.name == "issuesmith"
        assert cfg.label_namespace == "issuesmith"
        assert cfg.reset_label == "issuesmith:reset"
        assert cfg.transitions is not None
        assert "issuesmith:draft-running" in cfg.transitions
        assert "issuesmith:draft-done" in cfg.transitions["issuesmith:draft-running"]

    def test_load_issuesmith_roles_and_step_role(self, tmp_path: Path) -> None:
        wf_dir = _issuesmith_fixture(tmp_path, inject_roles=True)
        configs = load_workflows(wf_dir)
        cfg = configs[0]
        assert cfg.roles == {"design": ["claude", "codex"], "impl": ["cursor", "claude"]}
        first_steps = next(iter(cfg.handlers.values())).steps
        assert first_steps[0].role == "design"

    def test_validate_workflow_roles_on_load_rejects_undeclared(self, tmp_path: Path) -> None:
        yml = """\
name: roles-wf
template_dir: templates
triggers:
  - label: "pipe:ready"
    handler: run
handlers:
  run:
    steps:
      - id: s1
        template: step
        model: m
        role: unknown_role
roles:
  design:
    - claude
"""
        (tmp_path / "wf.yml").write_text(yml, encoding="utf-8")
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "step.md").write_text("# step\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unknown_role"):
            load_workflows(tmp_path)

    def test_state_machine_load_no_longer_needs_replace_workaround(self, tmp_path: Path) -> None:
        """_parse がフィールドを読むため、_load_workflow_config は replace なしで同等。"""
        wf_dir = _issuesmith_fixture(tmp_path)
        yml_path = wf_dir / "issuesmith.yml"
        via_state_machine = _load_workflow_config(yml_path)
        data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        via_parse = _parse(data, workflow_dir=wf_dir.resolve())
        assert via_state_machine.label_namespace == via_parse.label_namespace == "issuesmith"
        assert via_state_machine.transitions == via_parse.transitions
        assert via_state_machine.reset_label == via_parse.reset_label
