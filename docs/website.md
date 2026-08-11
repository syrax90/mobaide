# Marketing website (mobaide.com)

The public landing page at the apex domain. It exists because directories,
aggregators and social cards scrape the apex and its Open Graph tags — before
this page, `https://mobaide.com/` answered `{"detail":"Not Found"}`.

It is served by `central_server`, not by a separate static host, so it ships and
deploys with the relay and needs no extra infrastructure.

**This doc also governs the design system itself, not just the landing page.**
`site.css` is shared by the apex page *and* the end-user portal (`/portal/**`,
`/privacy.html`) — see *Portal styling* below. The internal admin dashboard
(`/dashboard/**`, `templates/base.html`) is a separate light Bootstrap-flavoured
sheet (`static/style.css`) and is deliberately not part of this system.

**A third consumer mirrors the tokens: the web SPA** (`web/src/styles/theme.css`,
see the Theming bullet in [`docs/web-client.md`](web-client.md)). Its dark theme
copies this sheet's `:root` values verbatim — **a token change in `site.css`
must be mirrored there** — and its light theme is a derived variant that exists
only in the SPA. Two invariants restate per-theme on that side: the brand
purples stay fill/border-only **on dark surfaces**, while on the SPA's light
theme `#6008ac` doubles as the accent *text* colour (8.97:1 on `#f4f4f4`); and
the light `--danger`/`--ok` are `#8c1d18`/`#116a2e`, because this sheet's
`#f2b8b5`/`#23d18b` are light-on-dark colours that fail on white. The SPA also
ships its own copy of the Departure Mono woff2 + OFL under `web/public/fonts/`
(different origin, same-origin CSP — it cannot load this host's copy).

## Where things live

| Path | What |
|---|---|
| `central_server/central_server/marketing/router.py` | Routes: `GET /`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`. Owns the response headers. |
| `central_server/central_server/marketing/loader.py` | Renders the page + crawler files once at startup. |
| `central_server/templates/marketing_landing.html` | **All page copy.** Edit here. |
| `central_server/static/site/site.css` | Stylesheet (the "modern pixel" design system) — **shared by the landing page and the portal.** Owns every token, the `@font-face`, the pixel kit, `.btn`, `.card` and the `.site-head`/`.site-foot` chrome. |
| `central_server/static/site/portal.css` | Portal-only additions: forms, alerts, tables, modals, choice fieldsets, legal-doc layout. Loaded **after** `site.css`. Declares no tokens and no `@font-face`. |
| `central_server/templates/portal_base.html` | Portal shell — loads both sheets and carries the shared chrome markup. |
| `central_server/static/style.css` | **Admin dashboard only.** Light sheet (`--primary: #0d6efd`, `--radius: 6px`), not part of this design system. |
| `central_server/static/site/site.js` | Reveal-on-scroll + hero typewriter. Landing page only — the portal deliberately does not load it. |
| `central_server/static/site/img/`, `fonts/` | Assets — see *Regenerating assets*. |
| `central_server/tests/test_marketing.py` | Route, header, and copy-accuracy tests. |
| `central_server/tests/test_portal_assets.py` | Portal asset stamping + the `portal.css` invariants. |
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
| "end-to-end" (auth or encryption) | Transport is TLS per hop; the relay terminates TLS and forwards plaintext through in-memory routing. There is no E2E crypto anywhere in the repo. | "Going through the relay — the default — every hop is TLS-encrypted (WSS)", and name the two auth mechanisms separately. **Both halves need the qualifiers in the last two rows** — do not write a bare "every hop is TLS-encrypted" or "JWT auth on every hop". |
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

`marketing/router.py` sets only `Cache-Control: public, max-age=300` on `/` (same
rationale as `/privacy.html`) and the two crawler/favicon TTLs.

Everything else is set site-wide by
`central_server/central_server/security_headers.py`, which serves this router's
paths — `/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, plus `/privacy.html`:

```
Content-Security-Policy: default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin-allow-popups
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), identity-credentials-get=()
```

The first three are the policy the router used to attach per response. The last
three the router never set — `X-Frame-Options` has only ever come from the
middleware — so they are site-wide additions, and their values and per-surface
variants live under "Security response headers" in
[`docs/deployment.md`](deployment.md).

**The CSP above must not be loosened**, and on the four marketing paths it is the
one thing `CSP_REPORT_ONLY` cannot de-enforce — they already ran it enforced in
production, so a soak has nothing to learn from them. (`/privacy.html` shares the
policy but not that exemption: it carried no security header before the
middleware, so it soaks like any other new surface.) It is the strictest of the
four policies the middleware emits, and the landing page satisfies it with no
relaxation at all:
its CSS and JS are external same-origin files, its icons are inline `<svg>`
markup rather than `data:` URIs, its font is same-origin, and the JSON-LD block
is a data block CSP does not treat as executable script. The tests in
`central_server/tests/test_marketing.py` pin the exact header values and scan the
rendered page for inline `<script>` and `style=`; they are what stops a copy edit
from quietly needing `'unsafe-inline'`.

The portal and dashboard get their own, looser policies — see the per-surface
table under "Security response headers" in
[`docs/deployment.md`](deployment.md). Making that possible is why no template in
this repo contains an inline `<script>`, an `onclick=` or an `hx-on:` any more:
the portal's behaviour lives in `static/site/portal.js`, the dashboard's in
`static/dashboard.js`.

`/robots.txt` disallows only `/api/` and `/portal/`, with one carve-out: an
`Allow: /portal/delete-account-info` line **emitted before the disallows**. That
page is anonymous, is the URL submitted to Google Play Console's Data Safety
form, and is linked from this site's own footer, so the blanket `/portal/` block
must not swallow it — RFC 9309 §2.2.2 longest-match makes the specific rule win,
and both Google and Bing honour it. `/portal/login` stays blocked on purpose.

It deliberately does **not**
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
| `static/htmx.min.js` | `no-cache` | **Do not raise this.** Both base templates pin an SRI `integrity` hash on it and the filename carries no version. If a client may reuse a cached copy without revalidating, an htmx upgrade hands it new HTML plus stale JS, the hashes disagree, and the browser blocks htmx — every portal *and dashboard* form silently breaks until the entry expires. `no-cache` still stores and still 304s; it only forbids reuse without asking. |
| `static/dashboard.js` | `no-cache` | **Do not raise this either**, for an unrelated reason: it contracts with the dashboard templates through `data-` attributes (`data-user-id`, `data-no-row-toggle`, `data-confirm-target`, `data-modal-close`) and the filename carries no version. Rename one on both sides and an admin holding a cached copy gets new markup plus stale JS — rows stop expanding, modals stop dismissing, the delete-user confirm gate stops arming — with no server-side symptom. `site/portal.js` needs no entry here: the tree-wide `?v=` hash below busts it. |
| `static/site/**` | 7 days | Versioned by a `?v=` query the templates append, so an asset edit busts them immediately. Covers `portal.css` too — which is exactly why it lives here rather than beside `style.css`: the hash spans the whole tree, so editing it busts both sheets automatically. |
| `static/style.css` (admin dashboard) | 1 hour | Referenced without a version query, so the TTL is what propagates a deploy. |

That `?v=` value is a **content hash over the whole `static/site/` tree**
(`marketing.loader.asset_version`), not the package version. It has to be:
`deploy_central.py` ships whatever is in the working tree, while `__version__`
only moves when release-please bumps it — so two deploys can carry different
assets under one version, and a repeat visitor would pair fresh HTML (5-minute
TTL) with week-stale CSS or images. If the tree is missing the helper degrades to
the package version rather than failing startup.

It is computed once in `create_app()` and hoisted to `app.state.asset_version`,
because **three** render paths now stamp it: `marketing.loader.render_landing` at
startup, `portal/router.py::_portal_context`, and `legal/router.py`. All three
must stamp the identical query — a divergent one re-keys the cache entries per
surface, so a landing → portal navigation refetches a 7-day-cached `site.css` and
wordmark the browser already holds. Note that the two routers use
`Jinja2Templates`' permissive default `Undefined` (unlike `marketing/loader.py`,
which uses `StrictUndefined`), so a forgotten context key degrades to a bare
`?v=` **silently** rather than raising. `test_portal_assets.py` is the guard.

It hashes the **whole tree**, not an enumerated file list, on purpose: an
over-broad hash only busts a few extra URLs, whereas a list that drifts out of
sync with the template silently reintroduces the week-stale bug for whatever it
missed — which is exactly what happened when it hashed only `site.css` and
`site.js` while the template also stamped `?v=` on seven images.

Three `static/site/` assets carry **no** `?v=` query, each deliberately:

- **The font**, in **all three** places it appears: the `@font-face` `url()` in
  `site.css`, the `<link rel="preload">` in `marketing_landing.html`, and the
  `<link rel="preload">` in `portal_base.html`. Required, not an oversight:
  preload matching keys on the full URL including the query string, and a static
  stylesheet cannot carry a template variable — so a `?v=` on a preload alone
  would download the 22.5 KB font twice and defeat the preload. Keep all three
  URLs byte-identical and rename the file if it is replaced.
- **`og.png`**, referenced only from `og:image` / `twitter:image` / JSON-LD. It is
  fetched by third-party scrapers, never by a visitor, and those platforms cache
  OG data in their own scrape caches independently of `Cache-Control` — so a
  query string would re-key a 144 KB image for conservative scrapers on every
  deploy that touches any site asset, while doing nothing about the cache that
  actually holds a stale card. Swap artwork with a platform re-scrape tool.
- **`favicon.ico`**, which is also served from the root `/favicon.ico` route.

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

The budget covers the **landing page only**. Portal pages ship none of the
screenshots but do add the vendored htmx bundle (~52 KB), `portal.js` (~13 KB)
and `portal.css` (~23 KB raw, ~7.6 KB gzipped — comments are only ~1 KB of that;
the bulk is the form, table, modal and legal-document rules, in the same
expanded one-property-per-line format as `site.css`). Every portal page is well
inside 200 KB. If `portal.css` starts approaching `site.css`'s size, that is the
signal a rule belongs in `site.css` instead.

## Accessibility invariants

- **`--brand` (`#6008AC`) and `--brand-2` (`#6650A4`) are fill and border
  colours only.** `#6650A4` on `#1E1E1E` is ~2.6:1 and fails WCAG AA. Text uses
  `--accent` / `--text` / `--text-muted`; the worst text pair on the page is
  4.6:1. Re-check any new pair before merging. The portal components honour this
  by keeping the purples structural: the checked choice-row ring, the radio's
  checked fill, and `.h-pixel`'s underline bar.
- **Form-control boundaries use `--gray` (`#8B8B8B`, 5.26:1 on `--bg-2`), not
  `--rule` (`#625B71`, 2.78:1).** WCAG 1.4.11 wants 3:1 for the boundary of a UI
  component the user has to find, and `--rule` misses it. `--rule` remains correct
  for decorative frames and plates (`.px-frame`, `.chip`, `.alert`).
- **`--danger` (`#F2B8B5`, 10.5:1) and `--ok` (`#23D18B`, 9.0:1)**, both measured
  on `--bg-2`, are used as text *and* as 2px ring fills. They are light-on-dark on
  purpose: the obvious dark alternative, a `#8C1D18` error container, measures
  1.83:1 as a ring on `--surface` and would fail 1.4.11.
- The wordmark keeps its light plate because it is dark purple ink drawn for a
  light background (it is lifted from the app icon).
- All motion is gated on `prefers-reduced-motion: reduce`.
- Animation start-states are gated on a `js` class that `site.js` sets from
  `<head>`, and the hero transcript's hidden state self-releases on every exit
  path (finished, reduced-motion, no `IntersectionObserver`, or a 2.5s
  watchdog). **Never add a hidden-by-default state that only a successful
  animation can undo.**

## Portal styling

The end-user portal (`/portal/**` and `/privacy.html`, everything extending
`templates/portal_base.html`) renders in this same design system, so a visitor
crossing from the landing page into sign-in sees no seam.

**Two sheets, in this order:**

1. `static/site/site.css` — tokens, `@font-face`, the pixel kit, `.btn` /
   `.btn-ghost`, `.card`, and the `.site-head` / `.site-foot` chrome.
2. `static/site/portal.css` — only what `site.css` lacks, because the landing page
   has no forms: inputs, labels, radios, alerts, the auth divider, the password
   reveal, tables, choice fieldsets, modals, and the legal-document layout.

**Placement rule:** a rule any dark Mobaide surface could want goes in `site.css`;
a rule only a form or portal page wants goes in `portal.css`. `portal.css` declares
no `:root` block and no `@font-face` — duplicating either is the drift that reusing
`site.css` exists to prevent. `test_portal_assets.py` enforces that, the
no-non-zero-`border-radius` rule, the brand-as-fill-only rule, and the
`--gray` form-boundary rule.

**Button vocabulary.** `style.css` and `site.css` disagree about what bare `.btn`
means — in the old light sheet it was the *secondary* outline button, here it is
the *filled brand* one. The portal uses:

| Class | Role |
|---|---|
| `.btn` | Primary action (Sign in, Create account, Delete confirm-step) |
| `.btn btn-ghost` | Secondary / Cancel / Dismiss / No |
| `.btn btn-danger` | Destructive (Delete my account) |
| `.btn btn-sm` | Small mono chrome action, e.g. header Sign out |

There is no `.btn-primary` — reintroducing it would silently render an unstyled
button. `.btn:disabled` is not polish: three portal submits start `disabled`, and
without it a brand-filled button looks fully enabled.

`site.js` is deliberately **not** loaded in the portal. Nothing there uses
`[data-reveal]` or `[data-typewriter]`, and its first act is to set a `js` class
that would arm `site.css`'s `opacity: 0` animation start-states. Without that
class no `site.css` motion rule can ever hide portal content.

**`static/site/portal.js`, by contrast, is loaded on every portal page.** It
holds every behaviour the portal templates
used to ship as an inline `<script>` or an `onclick=`: the password reveal, modal
dismissal, the password-match indicator, the Google Sign-In and Sign in with
Apple bootstraps, and the delete-account modal controller. The two providers
differ in shape: Google renders its own button and hands back a credential,
whereas Apple only supplies the flow, so `portal.css` draws the `.apple-btn`
itself (black fill, inline mark, no offset shadow — Apple's guidelines do not
permit decorating it) and `portal.js` ships it `disabled` until the SDK
answers. It loads after htmx and registers its
per-element initialisers through `htmx.onLoad`, so swapped-in fragments (the two
modals) get wired too; each initialiser is idempotent behind a `data-*-ready`
marker. Per-request values reach it as `data-` attributes rather than being
spliced into JS. This is a hard requirement of the portal CSP, not a style
preference — `script-src` there has no `'unsafe-inline'`, so inline code on these
pages does not run at all. The same applies to the admin dashboard and
`static/dashboard.js`.

Both base templates also set `includeIndicatorStyles: false` in their
`htmx-config` meta, because htmx otherwise injects an inline `<style>` block at
init that would force `'unsafe-inline'` into `style-src` on every htmx page. The
three rules it would have written are in `portal.css` and `style.css` instead —
delete them and any future `hx-indicator` is silently dead.

`.badge*`, `.info-grid`, `.qr-*`, `.token-display`, `.search-bar`, `.user-row` and
`.detail-row` are intentionally **not** ported: no portal template uses them, and
they stay in `static/style.css` for the admin dashboard.

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
(gray cloud) so ACME HTTP-01 can complete; under `CloudflareOrigin` it is
proxied and served by the origin certificate, which must therefore carry
`www.mobaide.com` as an explicit SAN. Do **not** cover it with a `*.mobaide.com`
wildcard: that SAN also matches `app.mobaide.com`, which silently disables ACME
for the SPA host — see [TLS certificates](deployment.md#tls-certificates).

If CLA-167 (per-host Caddy site blocks) lands later, fold this block into its
structure rather than keeping a fourth top-level block.
