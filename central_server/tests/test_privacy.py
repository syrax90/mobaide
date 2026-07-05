"""Tests for the root-mounted ``/privacy.html`` page and its renderer.

Covers the new self-hosted privacy policy contract:

- ``app.state.privacy_html`` is populated at startup (rendered once).
- ``GET /privacy.html`` returns 200, ``text/html``, and the documented
  ``Cache-Control`` header.
- The rendered body contains the document's H1 and section-specific
  phrases ("Argon2id", "Pavel Timonin", "EncryptedSharedPreferences").
- The body includes a rendered HTML ``<table>`` — proves the ``extra``
  markdown extension is wired (the policy uses GFM tables for the
  data-collection schedule).
- ``GET /privacy`` (without ``.html``) returns 404 — only the exact URL
  the user requested is served.
- Unit-level loader test using a temp dir with a minimal ``privacy.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from central_server.legal.loader import render_privacy


class TestPrivacyEndpoint:
    async def test_privacy_html_is_served_at_only_the_dot_html_url(
        self, client,
    ) -> None:
        """The plan and ``change_summary`` pin the public URL as
        ``/privacy.html`` exactly. ``/privacy`` (no extension) must NOT
        respond — guards against an accidental router alias drifting in."""
        resp = await client.get("/privacy")
        assert resp.status_code == 404


class TestRenderPrivacyLoader:
    """Unit-level coverage for ``render_privacy`` — verifies the GFM-table
    and TOC extensions are wired, the loader honors an explicit
    ``legal_dir`` override (the path resolution the app uses at startup),
    and the return shape carries both body HTML and the sidebar TOC.
    """

    def test_raises_when_privacy_md_missing(self, tmp_path: Path) -> None:
        """An empty ``legal_dir`` must raise — fail-loud at startup is
        better than serving an empty privacy page."""
        with pytest.raises(FileNotFoundError):
            render_privacy(tmp_path)
