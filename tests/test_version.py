"""Test that __version__ matches the version declared in pyproject.toml."""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path


def test_version_matches_pyproject():
    """__version__ must match the version in pyproject.toml."""
    import ghdag

    metadata_version = version("ghdag")
    assert ghdag.__version__ == metadata_version, (
        f"ghdag.__version__ ({ghdag.__version__!r}) != "
        f"importlib.metadata version ({metadata_version!r}). "
        "Update __init__.py or reinstall the package."
    )


def test_pyproject_version_format():
    """pyproject.toml version must be a valid 0.Y.Z semver string."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "Could not find version in pyproject.toml"
    ver = m.group(1)
    assert re.fullmatch(r"0\.\d+\.\d+", ver), (
        f"Version {ver!r} does not match 0.Y.Z format"
    )
