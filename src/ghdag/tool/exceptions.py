"""ghdag.tool.exceptions — Tool registry exceptions."""

from ghdag.exceptions import GhdagError


class ToolRegistryError(GhdagError):
    """Tool registry consistency error (file naming violation / duplicate definition)."""

    code = "TOOL_REGISTRY_INVALID"
    exit_code = 3
