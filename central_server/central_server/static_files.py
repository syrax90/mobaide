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

# Design-system assets — shared by the landing page and the account-deletion and
# legal pages — are versioned by a ``?v=`` query both shells append, whose value
# is a content hash over the whole ``site/`` tree (``marketing.loader.asset_version``)
# — so an asset edit busts them immediately and this TTL only bounds how long an
# unversioned request may serve a stale copy. That whole-tree hash is also why
# ``portal.css`` lives here rather than beside ``style.css``: every edit to it
# busts both sheets automatically. Kept off ``immutable`` deliberately: the
# filenames themselves are not content-hashed, so an in-place edit to an
# immutable asset would be uncorrectable for a year.
_SITE_CACHE_CONTROL = "public, max-age=604800"

# The ADMIN DASHBOARD sheet (``static/style.css``, loaded by
# ``templates/base.html``) is referenced without a version query, so it gets a
# TTL short enough that a deploy propagates on its own. The pages extending
# ``portal_base.html`` deliberately do not live here: they load ``site/site.css``
# + ``site/portal.css``, both stamped with the content hash, which puts them in
# the versioned tier above.
_DEFAULT_CACHE_CONTROL = "public, max-age=3600"

# Two unversioned filenames whose bytes must stay in lockstep with per-request
# HTML, for different reasons:
#
#   * ``htmx.min.js`` — loaded by ``templates/base.html`` with a pinned SRI
#     `integrity` hash. If a client may reuse a cached copy WITHOUT
#     revalidating, an htmx upgrade — which changes both the bytes and the hash
#     in the HTML — hands that client new HTML plus stale JS, the hashes
#     disagree, and the browser blocks htmx outright: every form silently stops
#     working until the entry expires.
#   * ``dashboard.js`` — the admin dashboard's behaviour, which used to be an
#     inline <script> in users.html and so could never drift from the markup
#     that shipped it. It now contracts with that markup through `data-`
#     attributes (`data-user-id`, `data-no-row-toggle`, `data-confirm-target`,
#     `data-modal-close`). Rename one on both sides and an admin holding a
#     cached copy gets new markup plus stale JS: rows stop expanding, modals
#     stop dismissing, the delete-user confirm gate stops arming — with no
#     server-side symptom. ``site/site.js`` is immune because the whole-tree
#     `?v=` hash busts it; this file sits at the static root and has no such
#     query.
#
# `no-cache` still stores the response and still gets the 304 bandwidth saving;
# it only forbids reuse without asking, which is the property both cases need.
_REVALIDATE_CACHE_CONTROL = "no-cache"
_MUST_REVALIDATE_ASSETS = frozenset({"htmx.min.js", "dashboard.js"})

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
    if normalised.rpartition("/")[2] in _MUST_REVALIDATE_ASSETS:
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
