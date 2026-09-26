"""FTTH planning backend. The version has one source: pyproject.toml."""
import tomllib
from importlib import metadata
from pathlib import Path


def _version():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.is_file():  # Source checkout: never trust a stale install record.
        return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    return metadata.version("ftth-demo0")


__version__ = _version()
