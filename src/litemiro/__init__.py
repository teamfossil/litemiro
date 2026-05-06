"""litemiro — Mirofish Phase 2 simulation engine + Phase 2→3 JSONL contract."""

from importlib.metadata import PackageNotFoundError, version

try:  # pragma: no cover - trivial fallback for editable installs
    __version__ = version("litemiro")
except PackageNotFoundError:
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
