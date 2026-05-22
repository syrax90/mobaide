"""Locate the bundled ``legal/`` directory and pre-render markdown documents.

Mirrors :mod:`central_server.templates_dir`: prefers ``cwd/legal`` so the
Docker image layout (``COPY legal/ legal/`` in the runtime stage) wins over
the source-tree sibling. Rendering happens once at app startup so per-request
cost is a single Jinja template render of an already-built HTML string.
"""
from __future__ import annotations

from pathlib import Path

import markdown

_PRIVACY_FILENAME = "privacy.md"

_MARKDOWN_EXTENSIONS = ("extra", "sane_lists")


def resolve_legal_dir() -> Path:
    """Return the ``legal/`` directory, preferring ``cwd/legal`` over the source-tree sibling."""
    cwd = Path.cwd() / "legal"
    if cwd.is_dir():
        return cwd
    return Path(__file__).resolve().parent.parent.parent / "legal"


def render_privacy_html(legal_dir: Path | None = None) -> str:
    """Read ``privacy.md`` and convert it to an HTML fragment.

    The ``extra`` extension enables GFM-style tables (the policy uses many);
    ``sane_lists`` stops mixed numbered/bulleted blocks from collapsing.
    """
    target_dir = legal_dir if legal_dir is not None else resolve_legal_dir()
    source = (target_dir / _PRIVACY_FILENAME).read_text(encoding="utf-8")
    return markdown.markdown(source, extensions=list(_MARKDOWN_EXTENSIONS))
