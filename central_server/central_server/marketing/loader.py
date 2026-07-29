"""Load the landing page, its crawler files and its icon once at startup.

Mirrors :mod:`central_server.legal.loader`: everything here runs a single time
during ``create_app`` so per-request cost is one string or bytes copy rather
than a template render or a disk read. ``robots.txt`` and ``sitemap.xml`` are
small enough to build from f-strings; only the page itself needs Jinja.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ..templates_dir import resolve_templates_dir

_TEMPLATE_NAME = "marketing_landing.html"
_FAVICON_RELATIVE_PATH = ("site", "img", "favicon.ico")

# The two site assets that change with any edit and are cached for 7 days, so
# their URLs must change whenever their bytes do.
_VERSIONED_ASSETS = (("site", "site.css"), ("site", "site.js"))

# Only the two surfaces a crawler could actually reach and index. `/privacy.html`
# is deliberately left crawlable — the Play listing links to it.
#
# Deliberately NOT listed: `/dashboard/`, `/docs`, `/redoc`, `/openapi.json`.
# Nothing public links to them, so a `Disallow` line buys no crawl budget, and
# naming them would publish the admin login's location and work against
# ``app._require_docs_admin``, which answers 404 on every rejection precisely so
# that a probe cannot tell docs-enabled-but-gated from docs-disabled. The same
# robots.txt is also served at api.mobaide.com (both hosts share one Caddy site
# block), which is exactly where a docs surface would live.
_DISALLOWED = ("/api/", "/portal/")


def render_landing(
    site_origin: str,
    asset_version: str,
    *,
    templates_dir: Path | None = None,
) -> str:
    """Render the landing page to a complete HTML document.

    ``site_origin`` is the scheme-and-host the page advertises in its canonical,
    Open Graph, and JSON-LD URLs; ``asset_version`` is appended to same-origin
    asset URLs as a cache-busting query.

    Raises ``FileNotFoundError`` when the template is missing. Failing loudly at
    startup matches ``render_privacy`` and is much better than a deployment that
    boots and then serves an empty home page.
    """
    target = templates_dir if templates_dir is not None else resolve_templates_dir()
    template_path = target / _TEMPLATE_NAME
    if not template_path.is_file():
        raise FileNotFoundError(f"landing template missing: {template_path}")

    env = Environment(
        loader=FileSystemLoader(str(target)),
        # Autoescaping is what keeps an operator-supplied SITE_BASE_URL from
        # breaking out of the JSON-LD block or an attribute.
        autoescape=select_autoescape(default_for_string=True, default=True),
        undefined=StrictUndefined,
    )
    return env.get_template(_TEMPLATE_NAME).render(
        site_origin=site_origin,
        asset_version=asset_version,
    )


def asset_version(static_dir: Path, fallback: str) -> str:
    """Short content hash over the versioned site assets, for cache busting.

    Deliberately NOT the package version: ``deploy_central.py`` tars whatever is
    in the working tree, while ``__version__`` only moves when release-please
    bumps it. Two deploys can therefore ship different ``site.css``/``site.js``
    under the same version — and since those carry a 7-day TTL, a repeat visitor
    would pair fresh HTML (5-minute TTL) with week-stale CSS. Hashing the bytes
    makes the query change exactly when the bytes do.

    Falls back to ``fallback`` (the package version) if an asset is missing, so a
    partial checkout degrades to the old behaviour instead of failing startup.
    """
    digest = hashlib.sha256()
    for parts in _VERSIONED_ASSETS:
        path = static_dir.joinpath(*parts)
        if not path.is_file():
            return fallback
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def read_favicon(static_dir: Path) -> bytes | None:
    """Read the site icon once at startup, or ``None`` when it is absent.

    Unlike the page, a missing icon is not worth failing startup over — the
    route degrades to a 404. Pre-reading keeps the handler off the event loop:
    it is anonymous and crawler-facing (clients request ``/favicon.ico`` whether
    or not the document declares one), and ``FileResponse`` would otherwise
    ``stat`` and re-read 4 KB through the thread pool on every hit.
    """
    path = static_dir.joinpath(*_FAVICON_RELATIVE_PATH)
    if not path.is_file():
        return None
    return path.read_bytes()


def render_robots(site_origin: str) -> str:
    disallow = "\n".join(f"Disallow: {path}" for path in _DISALLOWED)
    return (
        "User-agent: *\n"
        "Allow: /\n"
        f"{disallow}\n"
        "\n"
        f"Sitemap: {site_origin}/sitemap.xml\n"
    )


def render_sitemap(site_origin: str) -> str:
    """Sitemap covering the two public, indexable pages.

    ``lastmod`` is deliberately omitted rather than stamped at render time: the
    page is pre-rendered per process start, so a startup timestamp would claim
    the content changed on every restart.
    """
    # XML-escaped, unlike the HTML path where Jinja autoescaping covers it. An
    # `&` in the configured origin would otherwise emit XML that every crawler
    # rejects outright.
    urls = "".join(
        f"<url><loc>{xml_escape(site_origin + path)}</loc>"
        f"<changefreq>{freq}</changefreq>"
        f"<priority>{priority}</priority></url>"
        for path, freq, priority in (
            ("/", "weekly", "1.0"),
            ("/privacy.html", "monthly", "0.3"),
        )
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}"
        "</urlset>"
    )
