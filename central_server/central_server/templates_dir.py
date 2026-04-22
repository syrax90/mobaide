"""Shared template-directory resolution for Jinja2 consumers.

Both the dashboard HTMX pages and the transactional email sender render
from ``central_server/templates/``. In a source checkout the directory
sits next to the package; in the Docker image it sits next to the working
directory. Returning one resolved path from here lets all callers stay
consistent without copy-paste drift.
"""
from __future__ import annotations

from pathlib import Path


def resolve_templates_dir() -> Path:
    """Return the templates directory path, preferring ``cwd/templates`` over the source-tree sibling.

    The preference order matches the Dockerfile layout, which only ships
    site-packages and not the source tree.
    """
    cwd = Path.cwd() / "templates"
    if cwd.is_dir():
        return cwd
    # Package dir -> parent (central_server) -> sibling templates/
    return Path(__file__).resolve().parent.parent / "templates"
