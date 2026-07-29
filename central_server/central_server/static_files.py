"""Static-file directory resolution and a cache-aware ``StaticFiles``.

Mirrors :mod:`central_server.templates_dir`: in a source checkout the directory
sits next to the package, and in the Docker image it sits next to the working
directory (``COPY static/ static/`` in the runtime stage), so ``cwd/static``
wins.
"""
from __future__ import annotations

from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Marketing-site assets are versioned by a ``?v=`` query the landing template
# appends, whose value is a content hash over the whole ``site/`` tree
# (``marketing.loader.asset_version``) — so an asset edit busts them immediately
# and this TTL only bounds how long an unversioned request may serve a stale
# copy. Kept off ``immutable`` deliberately: the filenames themselves are not
# content-hashed, so an in-place edit to an immutable asset would be
# uncorrectable for a year.
_SITE_CACHE_CONTROL = "public, max-age=604800"

# The dashboard/portal sheet is referenced without a version query, so it gets a
# TTL short enough that a deploy propagates on its own.
_DEFAULT_CACHE_CONTROL = "public, max-age=3600"

# `templates/portal_base.html` loads the vendored htmx with a pinned SRI
# `integrity` hash, and the filename carries no version or content hash. If a
# client may reuse a cached copy WITHOUT revalidating, then an htmx upgrade —
# which changes both the bytes and the hash in the HTML — hands that client new
# HTML plus stale JS, the hashes disagree, and the browser blocks htmx outright:
# every portal form silently stops working until the entry expires. `no-cache`
# still stores the response and still gets the 304 bandwidth saving; it only
# forbids reuse without asking, which is the property a pinned hash depends on.
_REVALIDATE_CACHE_CONTROL = "no-cache"
_SRI_PINNED_ASSETS = frozenset({"htmx.min.js"})

_SITE_PREFIX = "site/"


def resolve_static_dir() -> Path:
    """Return the static directory, preferring ``cwd/static`` over the source-tree sibling."""
    cwd = Path.cwd() / "static"
    if cwd.is_dir():
        return cwd
    # Package dir -> parent (central_server) -> sibling static/
    return Path(__file__).resolve().parent.parent / "static"


def _cache_control_for(path: str) -> str:
    # Starlette builds ``path`` with ``os.path.join``, so normalise separators
    # before the prefix test rather than assuming POSIX.
    normalised = path.replace("\\", "/")
    if normalised.rpartition("/")[2] in _SRI_PINNED_ASSETS:
        return _REVALIDATE_CACHE_CONTROL
    if normalised.startswith(_SITE_PREFIX):
        return _SITE_CACHE_CONTROL
    return _DEFAULT_CACHE_CONTROL


class CachedStaticFiles(StaticFiles):
    """``StaticFiles`` that emits ``Cache-Control``, which Starlette never does.

    Starlette's ``FileResponse`` sets only ``ETag`` and ``Last-Modified``, so
    without this every asset costs a revalidation round-trip on each page view.

    ``get_response`` is the override point rather than ``file_response``:
    ``get_response(path, scope)`` has been stable across Starlette releases
    while ``file_response`` has gained parameters, and ``pyproject.toml`` pins
    no Starlette version. Wrapping the outer method also covers the 304 and
    directory-index paths, not just the plain file hit.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # Starlette raises HTTPException for a missing file or a bad method, so
        # this guard is belt-and-braces rather than the 404 path: it just keeps
        # an error response from ever being handed a long TTL.
        if response.status_code < 400:
            response.headers["Cache-Control"] = _cache_control_for(path)
        return response
