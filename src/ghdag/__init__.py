"""ghdag — Generic DAG execution engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ghdag")
except PackageNotFoundError:
    __version__ = "unknown"

# Prime io.queue before other subsystems import ghdag.quota (avoids io↔quota cycle).
import ghdag.io.queue as _queue  # noqa: F401
from ghdag.core.exceptions import GhdagError

__all__ = [
    "GhdagError",
]
