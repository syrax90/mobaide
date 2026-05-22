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

from central_server.legal.loader import render_privacy_html


class TestPrivacyEndpoint:
    async def test_privacy_html_returns_200_and_html_content_type(
        self, client,
    ) -> None:
        """The route is publicly reachable and serves HTML — Google Play /
        Caddy front-end must be able to hit it without auth."""
        resp = await client.get("/privacy.html")
        assert resp.status_code == 200
        # FastAPI / Starlette sets ``Content-Type: text/html; charset=utf-8``
        # for HTMLResponse; assert the prefix to stay tolerant of charset
        # ordering.
        assert resp.headers["content-type"].startswith("text/html")

    async def test_privacy_html_sets_documented_cache_control_header(
        self, client,
    ) -> None:
        """5-minute public cache — short enough that an emergency policy
        correction propagates quickly, long enough to absorb traffic
        spikes without re-rendering."""
        resp = await client.get("/privacy.html")
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=300"

    async def test_privacy_html_body_contains_document_h1(
        self, client,
    ) -> None:
        """The H1 from ``privacy.md`` must round-trip through markdown ->
        Jinja shell template to the response body."""
        resp = await client.get("/privacy.html")
        assert resp.status_code == 200
        body = resp.text
        # ``# Privacy Policy for Mobaide`` -> ``<h1>Privacy Policy for Mobaide</h1>``
        assert "<h1>Privacy Policy for Mobaide</h1>" in body

    async def test_privacy_html_body_contains_section_specific_phrases(
        self, client,
    ) -> None:
        """Unique strings from three distinct policy sections — confirms
        the full document (not just the header) reached the response."""
        resp = await client.get("/privacy.html")
        assert resp.status_code == 200
        body = resp.text
        # Section 1 (data controller).
        assert "Pavel Timonin" in body
        # Section 9 / security (password hashing).
        assert "Argon2id" in body
        # Section 9 / on-device storage.
        assert "EncryptedSharedPreferences" in body

    async def test_privacy_html_body_includes_rendered_html_table(
        self, client,
    ) -> None:
        """The policy uses GFM-style markdown tables for the data schedule
        in section 3. A rendered ``<table>`` element proves the ``extra``
        markdown extension is wired — without it those tables would
        render as raw pipe-and-dash text."""
        resp = await client.get("/privacy.html")
        assert resp.status_code == 200
        body = resp.text
        assert "<table>" in body

    async def test_privacy_html_is_served_at_only_the_dot_html_url(
        self, client,
    ) -> None:
        """The plan and ``change_summary`` pin the public URL as
        ``/privacy.html`` exactly. ``/privacy`` (no extension) must NOT
        respond — guards against an accidental router alias drifting in."""
        resp = await client.get("/privacy")
        assert resp.status_code == 404

    async def test_app_state_privacy_html_populated_at_startup(
        self, app,
    ) -> None:
        """``create_app`` renders the policy once into ``app.state.privacy_html``
        before any request fires. The state attribute must be a non-empty
        HTML string — the route depends on it via ``request.app.state``."""
        cached = app.state.privacy_html
        assert isinstance(cached, str)
        assert len(cached) > 0
        # It is HTML, not raw markdown — the H1 must be a real tag, not
        # ``# Privacy Policy ...``.
        assert "<h1>" in cached
        assert "Privacy Policy for Mobaide" in cached


class TestRenderPrivacyHtmlLoader:
    """Unit-level coverage for ``render_privacy_html`` — verifies the
    GFM-table extension is wired and the loader honors an explicit
    ``legal_dir`` override (the path resolution the app uses at startup).
    """

    def test_renders_tables_via_extra_extension(self, tmp_path: Path) -> None:
        """A minimal markdown with a pipe-table must produce an HTML
        ``<table>`` element. Without the ``extra`` extension the table
        would render as raw text."""
        (tmp_path / "privacy.md").write_text(
            "# Title\n\n"
            "| Col A | Col B |\n"
            "|---|---|\n"
            "| cell1 | cell2 |\n",
            encoding="utf-8",
        )
        html = render_privacy_html(tmp_path)
        assert "<table>" in html
        assert "<th>Col A</th>" in html
        assert "<td>cell1</td>" in html

    def test_renders_h1_from_markdown_header(self, tmp_path: Path) -> None:
        """``# Heading`` -> ``<h1>Heading</h1>``. Sanity check that the
        loader returns an HTML fragment, not the raw markdown."""
        (tmp_path / "privacy.md").write_text(
            "# My Heading\n\nsome body text.\n", encoding="utf-8",
        )
        html = render_privacy_html(tmp_path)
        assert "<h1>My Heading</h1>" in html
        assert "<p>some body text.</p>" in html

    def test_raises_when_privacy_md_missing(self, tmp_path: Path) -> None:
        """An empty ``legal_dir`` must raise — fail-loud at startup is
        better than serving an empty privacy page."""
        with pytest.raises(FileNotFoundError):
            render_privacy_html(tmp_path)
