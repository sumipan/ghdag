"""ghdag — Generic DAG execution engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ghdag")
except PackageNotFoundError:
    __version__ = "unknown"

from ghdag.core.exceptions import GhdagError

__all__ = [
    "GhdagError",
]
