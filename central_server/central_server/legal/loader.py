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

_MARKDOWN_EXTENSIONS = ("extra", "sane_lists", "toc")

_TOC_CONFIG = {
    "toc_depth": "2-2",
    "anchorlink": False,
    "permalink": False,
}


def resolve_legal_dir() -> Path:
    """Return the ``legal/`` directory, preferring ``cwd/legal`` over the source-tree sibling."""
    cwd = Path.cwd() / "legal"
    if cwd.is_dir():
        return cwd
    return Path(__file__).resolve().parent.parent.parent / "legal"


def render_privacy(legal_dir: Path | None = None) -> dict[str, str]:
    """Read ``privacy.md`` and convert it to an HTML fragment plus a TOC.

    Returns a mapping with two keys:

    - ``"html"`` — the rendered policy body. Headings at every level carry an
      ``id`` attribute (added by the ``toc`` extension) so jump links resolve.
    - ``"toc"`` — a standalone ``<div class="toc">…</div>`` containing only
      ``H2`` entries (``toc_depth="2-2"``) — 14 top-level sections, not the
      30+ subsection sprawl we'd get if H3s were included.

    The ``extra`` extension enables GFM-style tables (the policy uses many);
    ``sane_lists`` stops mixed numbered/bulleted blocks from collapsing.
    """
    target_dir = legal_dir if legal_dir is not None else resolve_legal_dir()
    source = (target_dir / _PRIVACY_FILENAME).read_text(encoding="utf-8")
    md = markdown.Markdown(
        extensions=list(_MARKDOWN_EXTENSIONS),
        extension_configs={"toc": _TOC_CONFIG},
    )
    html = md.convert(source)
    toc = getattr(md, "toc", "")
    return {"html": html, "toc": toc}
