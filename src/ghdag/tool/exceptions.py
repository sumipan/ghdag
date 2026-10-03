"""ghdag.tool.exceptions — Tool registry exceptions."""

from ghdag.exceptions import GhdagError


class ToolRegistryError(GhdagError):
    """Tool registry consistency error (file naming violation / duplicate definition)."""
