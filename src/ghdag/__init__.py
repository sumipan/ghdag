"""ghdag — Generic DAG execution engine."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("ghdag")
except PackageNotFoundError:
    __version__ = "unknown"
