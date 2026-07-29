# Marketing website (mobaide.com)

The public landing page at the apex domain. It exists because directories,
aggregators and social cards scrape the apex and its Open Graph tags — before
this page, `https://mobaide.com/` answered `{"detail":"Not Found"}`.

It is served by `central_server`, not by a separate static host, so it ships and
deploys with the relay and needs no extra infrastructure.

## Where things live

| Path | What |
|---|---|
| `central_server/central_server/marketing/router.py` | Routes: `GET /`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`. Owns the response headers. |
| `central_server/central_server/marketing/loader.py` | Renders the page + crawler files once at startup. |
| `central_server/templates/marketing_landing.html` | **All page copy.** Edit here. |
| `central_server/static/site/site.css` | Stylesheet (the "modern pixel" design system). |
| `central_server/static/site/site.js` | Reveal-on-scroll + hero typewriter. |
| `central_server/static/site/img/`, `fonts/` | Assets — see *Regenerating assets*. |
| `central_server/tests/test_marketing.py` | Route, header, and copy-accuracy tests. |
| `central_server/central_server/static_files.py` | `CachedStaticFiles` + static-dir resolution, shared with the dashboard mount. |

The page is **pre-rendered once in `create_app()`** into `app.state.marketing_html`
(alongside the privacy policy), so **a template or copy change needs a server
restart** to appear. CSS, JS and images are served from disk and do not.

## Changing the copy

Edit `marketing_landing.html`. Two constraints, both enforced by
`test_marketing.py` — if a test fails after a copy edit, the test is probably
right:

**1. No inline `style="…"` and no inline `<script>`.** The response carries
`Content-Security-Policy: default-src 'self'` with no `'unsafe-inline'`, so
either would be silently blocked. Icons go in as inline `<svg>` markup rather
than `data:` URIs for the same reason. A `<script type="application/ld+json">`
block is fine — CSP does not treat a data block as executable script.

**2. Accuracy gate.** Several claims in the Play Store listing are contradicted
by the code. The store copy is deliberately left as-is (out of scope for
CLA-170), but the website must not repeat them:

| Do not write | Why | Write instead |
|---|---|---|
| "end-to-end" (auth or encryption) | Transport is TLS per hop; the relay terminates TLS and forwards plaintext through in-memory routing. There is no E2E crypto anywhere in the repo. | "Every hop is TLS-encrypted (WSS)", "JWT auth on every hop" |
| "32 sessions **per agent type**", or 32 as a hard limit | `max_sessions` (`mobaide_agent/mobaide/core/session_manager.py`) is one global cap over a flat session dict, and it is configurable (`mobaide/agent/config.py`); 32 is the default. | "Up to 32 concurrent sessions on one agent" |
| "**each** session in its own dedicated Git worktree" | Worktrees are opt-in per launch and Git-only. A `worktree=true` request against a non-Git project logs a warning and falls back to the project root. | The worktree claim **with both qualifiers**: opt-in per launch, Git projects only |
| any implication that nothing is stored server-side | The relay persists accounts, paired devices, prompt templates, saved shortcuts and custom agent types. | "the relay never persists session traffic" + name what it does persist |
| "no inbound ports", "nothing listens on your machine" | The agent binds `0.0.0.0:8000` by default — `mobaide_agent/mobaide/agent/config.py:51`, whose own comment says it "binds every interface by default (DIRECT mode needs LAN reachability)". | "No public IP or port forwarding", "nothing has to be reachable from the internet" |
| "10,000-line scrollback" replays on reconnect | Two different buffers. The realtime WS path replays byte-exactly from a ring capped at `RING_MAX_BYTES` (4 MiB, `core/session.py`); the 10,000 figure is `scrollback_size`, a deque of read **chunks** that backs the REST `/scrollback` endpoint (see `get_scrollback`'s docstring). | "a rolling buffer of recent output" — state the guarantee, not a line count |
| "JWT auth on every hop" | Only the app→relay hop is a JWT. Each agent presents an opaque per-agent token verified by SHA-256 `compare_digest` (`auth/service.py` → `db.verify_agent_token`). | name the two mechanisms separately |
| "every hop is TLS-encrypted" **unqualified** | True for relay mode only. The optional direct/LAN mode builds `ws://` unless the agent sits behind HTTPS — `WebSocketManager.kt`: `if (agentUrl.startsWith("https")) "wss" else "ws"` — and the agent's own listener is plain HTTP by default. | scope it ("going through the relay — the default — every hop is…") and state the direct-mode caveat |

The worktree capability itself is broad and true, and the page says so: every
session kind supports it — Claude Code, Codex CLI, Antigravity CLI, raw
Terminal, and custom agent types (`core/claude_launcher.py`,
`core/codex_launcher.py`, `core/antigravity_launcher.py`, and
`core/session_manager.py::_resolve_worktree_cwd` for shell/custom kinds).

Spell the products **Claude Code**, **Codex CLI**, **Antigravity CLI**.

## Response headers

Set in `marketing/router.py`, **scoped to the marketing responses only**:

- `Cache-Control: public, max-age=300` on `/` (same rationale as `/privacy.html`).
- `Content-Security-Policy: default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'`
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`

**Do not promote this CSP to middleware.** `/portal/login` loads Google Sign-In
from `accounts.google.com` and `portal_base.html` contains inline `<script>` and
`style=` attributes, all of which this policy would block. Site-wide headers are
tracked separately in CLA-171.

`/robots.txt` disallows only `/api/` and `/portal/`. It deliberately does **not**
list `/dashboard/`, `/docs`, `/redoc` or `/openapi.json`: nothing public links to
them so a `Disallow` buys no crawl budget, and naming them would publish the
admin login's location and undercut `app._require_docs_admin`, which answers 404
on every rejection precisely so a probe cannot distinguish docs-enabled-but-gated
from docs-disabled. The same `robots.txt` is also served at `api.mobaide.com`
(both hosts share one Caddy site block). If those paths ever need a noindex
guarantee, use an `X-Robots-Tag: noindex` response header, not a public file.

### Static asset caching

`CachedStaticFiles` supplies the `Cache-Control` that Starlette's `StaticFiles`
omits entirely — without it every asset costs a revalidation round-trip. Three
tiers:

| Path | Value | Why |
|---|---|---|
| `static/htmx.min.js` | `no-cache` | **Do not raise this.** `portal_base.html` pins an SRI `integrity` hash on it and the filename carries no version. If a client may reuse a cached copy without revalidating, an htmx upgrade hands it new HTML plus stale JS, the hashes disagree, and the browser blocks htmx — every portal form silently breaks until the entry expires. `no-cache` still stores and still 304s; it only forbids reuse without asking. |
| `static/site/**` | 7 days | Versioned by a `?v=` query the template appends, so an asset edit busts them immediately. |
| everything else | 1 hour | Referenced without a version query, so the TTL is what propagates a deploy. |

That `?v=` value is a **content hash** over `site.css` + `site.js`
(`marketing.loader.asset_version`), not the package version. It has to be:
`deploy_central.py` ships whatever is in the working tree, while `__version__`
only moves when release-please bumps it — so two deploys can carry different CSS
under one version, and a repeat visitor would pair fresh HTML (5-minute TTL) with
week-stale CSS. If an asset is missing the helper degrades to the package version
rather than failing startup.

The font is the one `static/site/` asset with **no** `?v=` query, in both the
`<link rel="preload">` and the `@font-face` `url()`. That is required, not an
oversight: preload matching keys on the full URL including the query string, and
a static stylesheet cannot carry a template variable — so a `?v=` on the preload
alone would download the 22.5 KB font twice and defeat the preload. Keep the two
URLs byte-identical and rename the file if it is ever replaced.

## Canonical origin

`config.site_base_url` (`SITE_BASE_URL`, default `https://mobaide.com`) is the
origin used for the canonical link, OG/Twitter image URLs, JSON-LD and the
sitemap.

This is deliberately **not** `public_base_url`: that is the *relay* origin
embedded in QR pairing payloads, which production points at `api.mobaide.com`.
Reusing it would publish the API host as the site's canonical URL.

The value is shape-checked (`scheme://host[:port]`, no path) and a malformed one
falls back to the default rather than raising — a typo in a marketing URL should
not take the relay down. A trailing slash is stripped in both places.

## Weight budget

Keep the page under **200 KB** all-inclusive (raw, before Caddy's gzip). It is
currently ~187 KB, of which ~87 KB is the initial render — the four screenshots
are `loading="lazy"` below the fold. Measure by summing the HTML plus every
same-origin URL it references; the four WebP screenshots dominate, so that is
where to look first if the budget is at risk.

## Accessibility invariants

- **`--brand` (`#6008AC`) and `--brand-2` (`#6650A4`) are fill and border
  colours only.** `#6650A4` on `#1E1E1E` is ~2.6:1 and fails WCAG AA. Text uses
  `--accent` / `--text` / `--text-muted`; the worst text pair on the page is
  4.6:1. Re-check any new pair before merging.
- The wordmark keeps its light plate because it is dark purple ink drawn for a
  light background (it is lifted from the app icon).
- All motion is gated on `prefers-reduced-motion: reduce`.
- Animation start-states are gated on a `js` class that `site.js` sets from
  `<head>`, and the hero transcript's hidden state self-releases on every exit
  path (finished, reduced-motion, no `IntersectionObserver`, or a 2.5s
  watchdog). **Never add a hidden-by-default state that only a successful
  animation can undo.**

## Store badge rules

The Google Play badge (`img/google-play-badge.png`) is the unmodified official
asset from Google's Partner Marketing Hub — do not recolour, crop or re-render
it, and keep it no smaller than any other store badge on the page. The "iOS —
coming soon" element is a plain text chip on purpose: Apple's badge artwork may
not be imitated or altered, and the iOS client is not listed yet.

## Regenerating assets

Sources live outside the repo in
`~/Documents/ML/Projects/mobaide-related/Google Play Console/`. The generator is
a one-off script, not committed; recreate it when assets need to change:

1. `python3 -m venv <tmp>/venv && <tmp>/venv/bin/pip install pillow numpy`
2. Wordmark: lift the pixel wordmark off the app icon's white plate. Classify
   each pixel to its ink (`#6008AC` or `#8B8B8B`) by direction-from-white, take
   alpha from how far it travelled toward that ink, and zero any pixel with
   luminance ≥ 235 (the plate is a faint off-white gradient, so alpha
   thresholding alone cannot separate it from a light antialiased edge). Crop to
   the bbox — 454×81 — then box-downscale by 2. Ship one resolution only:
   `image-rendering: pixelated` wants a crisp nearest-neighbour upscale, so a 2x
   variant is both heavier and smoother than intended.
3. Screenshots: use `Screenshots/original/*.png` (bare app captures), **not** the
   `*_1080px.png` files — those are Play marketing composites with a headline and
   phone frame already baked in, which would double-frame against the CSS device
   frame. Resize to 420px wide, WebP quality 72.
4. Icons: `favicon.ico` multi-size 16/32/48; `apple-touch-icon.png` at 180px
   quantized to 64 colours.
5. OG card: 1200×630 **PNG or JPEG** (several scrapers reject WebP), composed
   from the wordmark on a light plate plus the terminal screenshot.
6. Font: `DepartureMono-Regular.woff2` from the
   [Departure Mono](https://github.com/rektdeckard/departure-mono) release,
   shipped whole at 22.5 KB with its OFL licence alongside. No subsetting step —
   at that size it is not worth a build dependency.

## Deployment

Manual, like the rest of the relay: an operator runs
`deploy/prod/deploy_central.py`. `.github/workflows/release-server.yml` only
records approval. **The apex 404 disappears only once that deploy runs.**

No deploy-script change was needed for the new assets: `Dockerfile` does
`COPY static/ static/` recursively, and `deploy_central.py`'s `TAR_EXCLUDES`
excludes no image, font or media extension.

### The `www` redirect

Both `deploy/prod/Caddyfile.LetsEncrypt` and `Caddyfile.CloudflareOrigin` now
carry a `www.mobaide.com` block that 308-redirects to the apex. Before it,
`www.mobaide.com` matched no site block and got Caddy's default empty 200 — a
bare 200 with a zero-byte body and no `Via: 1.1 Caddy` header, which is how you
can tell the request reached Caddy but never a `reverse_proxy`.

`www` needs a DNS record either way. Under `LetsEncrypt` it must be "DNS only"
(gray cloud) so ACME HTTP-01 can complete; under `CloudflareOrigin` the existing
wildcard origin certificate already covers it.

If CLA-167 (per-host Caddy site blocks) lands later, fold this block into its
structure rather than keeping a fourth top-level block.
