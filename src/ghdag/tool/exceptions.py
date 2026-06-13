"""ghdag.tool.exceptions — Tool registry exceptions."""

from ghdag.exceptions import GhdagError


class ToolRegistryError(GhdagError):
    """Tool レジストリの整合性エラー（ファイル名規約違反・多重定義）。"""
