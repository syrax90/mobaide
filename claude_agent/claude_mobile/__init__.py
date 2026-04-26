"""Claude Mobile agent package.

Exposes ``__version__`` resolved from the installed distribution metadata so
``FastAPI(version=...)``, ``/api/version`` and the wire-handshake
``agent_version`` field share a single source of truth (the distribution
version declared in ``pyproject.toml``).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("claude-mobile-agent")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
