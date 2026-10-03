"""
pipeline/order.py — template rendering

Ported from: tools/stash-developer/stash_developer/order_builder.py (interface only)
"""

from __future__ import annotations

import hashlib
import string
from pathlib import Path

from ghdag.core.ports.order import OrderBuilder

__all__ = ["OrderBuilder", "InlineOrderBuilder", "TemplateOrderBuilder", "TemplateVariableError"]


class TemplateVariableError(ValueError, KeyError):
    """Missing template variable error. Subclass of both ValueError and KeyError."""

    def __str__(self) -> str:
        return self.args[0] if self.args else ""


def _check_missing_vars(
    tmpl: string.Template,
    context: dict[str, str],
    source_label: str,
) -> None:
    required = set(tmpl.get_identifiers())
    provided = set(context.keys())
    missing = sorted(required - provided)
    if missing:
        raise TemplateVariableError(
            f"template render error ({source_label}): "
            f"undefined variables: {missing}, "
            f"available keys: {sorted(provided)}"
        )


class InlineOrderBuilder:
    """OrderBuilder that takes a template string directly and renders it with string.Template.

    It performs no file I/O, so it is used for dynamic prompt generation (e.g. scheduler).

    Dynamic prompts (e.g. via mltgnt skill action) may contain LLM-facing
    ``${ENV_VAR}`` notation in the prompt body. Strict rendering would treat it as an
    undefined variable and kill the scheduler job, so ``safe_substitute`` is used to
    leave undefined variables as ``${VAR}``. The file-based :class:`TemplateOrderBuilder`
    stays strict because there the template author writes placeholders intentionally.
    """

    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """Render step_id as the template body with string.Template.

        Args:
            step_id: template body (a file name for TemplateOrderBuilder, but the
                     template string itself for InlineOrderBuilder).
                     LLMPipelineAPI.submit passes StepConfig.template as-is.
            context: dict used to render template variables
        Returns:
            Rendered order body. Undefined variables and malformed placeholders
            (``${}`` etc.) are left as-is (``safe_substitute`` behavior). Never raises.
        """
        tmpl = string.Template(step_id)
        return tmpl.safe_substitute(context)


class TemplateOrderBuilder:
    """File-based OrderBuilder implementation rendering with string.Template."""

    def __init__(self, template_dir: str | Path):
        """
        Args:
            template_dir: directory path containing template files
        """
        self._template_dir = Path(template_dir)

    def build_order(self, step_id: str, context: dict[str, str]) -> str:
        """Read template_dir/{step_id}.md and render context with string.Template.

        Args:
            step_id: template file name (without extension)
            context: dict used to render template variables
        Returns:
            rendered order body (string)
        Raises:
            FileNotFoundError: template_dir/{step_id}.md does not exist
            ValueError: a template variable is missing from context, or invalid $ syntax
        """
        template_path = self._template_dir / f"{step_id}.md"
        if not template_path.exists():
            raise FileNotFoundError(
                f"template file not found: {template_path}"
            )
        tmpl = string.Template(template_path.read_text(encoding="utf-8"))
        _check_missing_vars(tmpl, context, str(template_path))
        try:
            return tmpl.substitute(context)
        except ValueError as e:
            raise ValueError(
                f"template render error ({template_path}): {e}"
            ) from e

    def get_template_hash(self, step_id: str) -> str:
        """Return SHA-256 hex digest of the template file content.

        Args:
            step_id: template file name (without .md extension)
        Returns:
            64-character lowercase hex string
        Raises:
            FileNotFoundError: template_dir/{step_id}.md does not exist
        """
        template_path = self._template_dir / f"{step_id}.md"
        if not template_path.exists():
            raise FileNotFoundError(
                f"template file not found: {template_path}"
            )
        return hashlib.sha256(template_path.read_bytes()).hexdigest()
