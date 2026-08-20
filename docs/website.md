# Marketing website (mobaide.com)

The public landing page at the apex domain. It exists because directories,
aggregators and social cards scrape the apex and its Open Graph tags — before
this page, `https://mobaide.com/` answered `{"detail":"Not Found"}`.

It is served by `central_server`, not by a separate static host, so it ships and
deploys with the relay and needs no extra infrastructure.

**This doc also governs the design system itself, not just the landing page.**
`site.css` is shared by the apex page *and* the three static pages under
`templates/portal_base.html` (`/privacy.html`, `/portal/delete-account-info`,
`/portal/cancel-deletion`) — see *Static-page styling* below. The internal admin dashboard
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
| `central_server/static/site/site.css` | Stylesheet (the "modern pixel" design system) — **shared by the landing page and the `portal_base.html` pages.** Owns every token, the `@font-face`, the pixel kit, `.btn`, `.card` and the `.site-head`/`.site-foot` chrome. |
| `central_server/static/site/portal.css` | Additions `site.css` lacks: forms, alerts, tables, choice fieldsets, legal-doc layout. Loaded **after** `site.css`. Declares no tokens and no `@font-face`. |
| `central_server/templates/portal_base.html` | Shell for the static pages — loads both sheets, carries the shared chrome markup, and loads **no script at all**. |
| `central_server/static/style.css` | **Admin dashboard only.** Light sheet (`--primary: #0d6efd`, `--radius: 6px`), not part of this design system. |
| `central_server/static/site/site.js` | Reveal-on-scroll, hero typewriter, and the install-command copy buttons. Landing page only — `portal_base.html` deliberately does not load it. |
| `central_server/static/site/img/`, `fonts/` | Assets — see *Regenerating assets*. |
| `central_server/tests/test_marketing.py` | Route, header, and copy-accuracy tests. |
| `central_server/tests/test_portal_assets.py` | Static-page asset stamping + the `portal.css` invariants. |
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
| "no inbound ports", "nothing listens on your machine" | The agent always starts a listener, however the app reaches it — on `0.0.0.0:8000` by default, or wherever `HOST`/`PORT` point (`mobaide_agent/mobaide/agent/config.py:51`, passed to uvicorn in `mobaide/__main__.py`). | "No public IP or port forwarding", "nothing has to be reachable from the internet" |
| "10,000-line scrollback" replays on reconnect | Two different buffers. The realtime WS path replays byte-exactly from a ring capped at `RING_MAX_BYTES` (4 MiB, `core/session.py`); the 10,000 figure is `scrollback_size`, a deque of read **chunks** that backs the REST `/scrollback` endpoint (see `get_scrollback`'s docstring). | "a rolling buffer of recent output" — state the guarantee, not a line count |
| "JWT auth on every hop" | Only the app→relay hop is a JWT. Each agent presents an opaque per-agent token verified by SHA-256 `compare_digest` (`auth/service.py` → `db.verify_agent_token`). | name the two mechanisms separately |
| "every hop is TLS-encrypted" **unqualified** | True for the relay path, which is the only one a client can take. The DIRECT-mode plumbing the clients still carry would build `ws://` unless the agent sits behind HTTPS — `WebSocketManager.kt`: `if (agentUrl.startsWith("https")) "wss" else "ws"` — and the agent's own listener is plain HTTP by default. | scope it ("going through the relay, every hop is…") — without describing a direct mode, see the next row |
| a "direct"/"LAN" mode, or "point the app straight at the agent" | No client exposes one. `AuthRepository.connectAgent()` (Android and iOS) has no UI caller — the Connections screen's "+" routes to the relay login — and the agent's `UserStore.add_user()` has no CLI verb or endpoint, so the user store `POST /api/auth/token` checks is always empty. The mode is code, not a feature. | say nothing about it; describe only the relay path |

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

The `portal_base.html` pages and the dashboard get their own policy — see the
per-surface table under "Security response headers" in
[`docs/deployment.md`](deployment.md). Making that possible is why no template in
this repo contains an inline `<script>`, an `onclick=` or an `hx-on:` any more:
the dashboard's behaviour lives in `static/dashboard.js`, and the three static
pages need no behaviour at all.

`/robots.txt` disallows only `/api/` and `/portal/`, with one carve-out: an
`Allow: /portal/delete-account-info` line **emitted before the disallows**. That
page is anonymous, is the URL submitted to Google Play Console's Data Safety
form, and is linked from this site's own footer, so the blanket `/portal/` block
must not swallow it — RFC 9309 §2.2.2 longest-match makes the specific rule win,
and both Google and Bing honour it. `/portal/cancel-deletion` stays blocked on
purpose: it carries a single-use token in its query string.

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
| `static/htmx.min.js` | `no-cache` | **Do not raise this.** `templates/base.html` pins an SRI `integrity` hash on it and the filename carries no version. If a client may reuse a cached copy without revalidating, an htmx upgrade hands it new HTML plus stale JS, the hashes disagree, and the browser blocks htmx — every dashboard form silently breaks until the entry expires. `no-cache` still stores and still 304s; it only forbids reuse without asking. |
| `static/dashboard.js` | `no-cache` | **Do not raise this either**, for an unrelated reason: it contracts with the dashboard templates through `data-` attributes (`data-user-id`, `data-no-row-toggle`, `data-confirm-target`, `data-modal-close`) and the filename carries no version. Rename one on both sides and an admin holding a cached copy gets new markup plus stale JS — rows stop expanding, modals stop dismissing, the delete-user confirm gate stops arming — with no server-side symptom. `site/site.js` needs no entry here: the tree-wide `?v=` hash below busts it. |
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
surface, so a landing → `/privacy.html` navigation refetches a 7-day-cached
`site.css` and wordmark the browser already holds. Note that the two routers use
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

## The browser-client origin

`config.web_app_base_url` (`WEB_APP_BASE_URL`, default `https://app.mobaide.com`)
is a **third** origin, and it is the one every account link on this host resolves
against: the header and footer "Sign in" buttons, the hero "Open the web app"
CTA, the feature card and FAQ entry that name the browser client, and the
cross-links in the two surviving `/portal/*` pages. It is hoisted to
`app.state.web_app_origin` alongside `asset_version`, for the same reason: the
landing pre-render, `portal/router.py::_portal_context` and `legal/router.py` all
stamp it, and they must all name the same host.

Set it to a Vite dev server (`WEB_APP_BASE_URL=http://localhost:5173`) to run the
whole marketing site against a local SPA. Same shape check and same
fall-back-on-malformed behaviour as `SITE_BASE_URL`.

The same `StrictUndefined`-vs-`Undefined` asymmetry applies as for `?v=`: the
landing page raises on a missing `app_origin`, while the two `Jinja2Templates`
routers would render `href="/login"` — a same-origin path that does not exist on
this host — so both are asserted in `test_marketing.py` and `test_portal.py`.

## Weight budget

Keep the page under **200 KB** all-inclusive (raw, before Caddy's gzip). It is
currently ~194 KB, of which ~96 KB is the initial render — the four screenshots
are `loading="lazy"` below the fold. Measure by summing the HTML plus every
same-origin URL it references; the four WebP screenshots dominate, so that is
where to look first if the budget is at risk.

**Headroom is now ~6 KB**, the 10.8 KB App Store badge having spent half of
what was left. The next asset of any size needs the screenshots re-encoded
first — they are ~100 KB of the total on their own.

The budget covers the **landing page only**. The `portal_base.html` pages ship
none of the screenshots and no JavaScript whatsoever; they add only `portal.css`
(~26 KB raw, ~8.5 KB gzipped — comments are only ~1 KB of that; the bulk is the
form, table and legal-document rules, in the same expanded
one-property-per-line format as `site.css`). All three are well inside 200 KB. If
`portal.css` starts approaching `site.css`'s size, that is the signal a rule
belongs in `site.css` instead.

## Accessibility invariants

- **`--brand` (`#6008AC`) and `--brand-2` (`#6650A4`) are fill and border
  colours only.** `#6650A4` on `#1E1E1E` is ~2.6:1 and fails WCAG AA. Text uses
  `--accent` / `--text` / `--text-muted`; the worst text pair on the page is
  4.6:1. Re-check any new pair before merging. The `portal.css` components honour
  this by keeping the purples structural: the checked choice-row ring, the
  radio's checked fill, and `.h-pixel`'s underline bar.
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

## Static-page styling

Three server-rendered pages extend `templates/portal_base.html` — `/privacy.html`,
`/portal/delete-account-info` and `/portal/cancel-deletion` — and render in this
same design system, so a visitor crossing from the landing page sees no seam.

They are what is left of the end-user portal. Sign-in, sign-up, verification,
password reset and the account page moved to the browser client at
`app.mobaide.com`; these two `/portal/*` pages stayed because external systems
name them (the deletion email links one, Google Play Console's Data Safety form
links the other). The shell's "Sign in" button and every cross-link in their copy
resolve against `{{ app_origin }}`, which comes from `WEB_APP_BASE_URL` — see
*The browser-client origin* above.

**Two sheets, in this order:**

1. `static/site/site.css` — tokens, `@font-face`, the pixel kit, `.btn` /
   `.btn-ghost`, `.card`, and the `.site-head` / `.site-foot` chrome.
2. `static/site/portal.css` — only what `site.css` lacks, because the landing page
   has no forms: inputs, labels, radios, alerts, tables, choice fieldsets and the
   legal-document layout.

**Placement rule:** a rule any dark Mobaide surface could want goes in `site.css`;
a rule only a form or document page wants goes in `portal.css`. `portal.css`
declares no `:root` block and no `@font-face` — duplicating either is the drift
that reusing `site.css` exists to prevent. `test_portal_assets.py` enforces that,
the no-non-zero-`border-radius` rule, the brand-as-fill-only rule, and the
`--gray` form-boundary rule.

`portal.css` still carries rules for components no surviving template uses (the
auth divider, the password reveal, the modal layer). They are kept rather than
pruned: the sheet is shared with `/privacy.html`, dead selectors cost nothing at
runtime, and the SPA's own stylesheet was derived from these.

The cost is bytes, and it has been measured: 58 of the file's 78 class tokens
match nothing the three pages emit — about 10.5 KB raw, ~3 KB gzipped, on a
7-day-cached sheet, so once per client per deploy. That is the budget a future
prune would recover; it is not a reason to prune it during an unrelated change.

**Button vocabulary.** `style.css` and `site.css` disagree about what bare `.btn`
means — in the old light sheet it was the *secondary* outline button, here it is
the *filled brand* one. These pages use:

| Class | Role |
|---|---|
| `.btn` | Primary action (Open the web app, Yes cancel deletion, Sign in) |
| `.btn btn-ghost` | Secondary / Cancel / Dismiss / No |
| `.btn btn-danger` | Destructive |
| `.btn btn-sm` | Small mono chrome action |

There is no `.btn-primary` — reintroducing it would silently render an unstyled
button.

**No JavaScript reaches these pages at all.** `site.js` is deliberately not
loaded: nothing here uses `[data-reveal]` or `[data-typewriter]`, and its first
act is to set a `js` class that would arm `site.css`'s `opacity: 0` animation
start-states, after which no `site.css` motion rule could ever un-hide the
content. Neither is htmx, nor any behaviour bundle, nor a sign-in SDK — those
existed for the retired account and login screens, and `/portal/delete-account-info`
was loading Google's and Apple's SDKs on every hit for nothing. The remaining
interactivity is one native form POST. Adding a `<script>` back here is a policy
change, not a tweak: `security_headers.py` now serves these pages a CSP that names
no third-party origin and grants no `'unsafe-inline'`.

The admin dashboard still loads htmx and `static/dashboard.js`, and still sets
`includeIndicatorStyles: false` in its `htmx-config` meta — htmx would otherwise
inject an inline `<style>` block at init that forces `'unsafe-inline'` into
`style-src`. The three rules it would have written live in `style.css`; delete
them and any future `hx-indicator` is silently dead.

`.badge*`, `.info-grid`, `.qr-*`, `.token-display`, `.search-bar`, `.user-row` and
`.detail-row` are intentionally **not** ported: they stay in `static/style.css`
for the admin dashboard.

## Copyable command blocks

The two install one-liners in step 01 each sit in their own `.copyblock` — a
label row carrying an icon-only copy button, over the framed `<pre class="code">`
holding the command. It is the marketing-side port of the clients' install
sheets; Android's `InstallCommandCard` is the closest mirror, an icon-only
button whose `contentDescription` names the OS.

**The command exists in exactly one place: the `<code>` element.** `site.js`
reads `code.textContent` at click time, and the button carries a bare `data-copy`
hook with no value. A `data-copy="curl …"` would be a second copy of the string
that can drift from the one on screen — a visitor would copy something other
than what they read, with nothing visibly wrong. `test_marketing.py` asserts
each command appears exactly once for that reason.

**The buttons are `display: none` until `site.js` sets `can-copy`** on the root
element, which it does only after the Clipboard API checks out and every button
is wired. Deliberately not gated on the `js` class: that says only that
scripting ran, while `navigator.clipboard` is undefined on an insecure origin —
so `js` alone would show a button that silently does nothing. A button that
cannot copy is worse than no button, and the command underneath is selectable
text either way. This is *not* the hidden-by-default state the accessibility
invariants forbid: `can-copy` is set synchronously from `init()` and the banned
pattern is a hidden state that only a *successful animation* can undo — no
animation is in this path at all. `setUpCopyButtons()` is likewise called
**before** `init()`'s reduced-motion early return, since copying is not motion.

Colour choices follow the invariants above rather than inventing any: the
button's boundary is `--gray` (WCAG 1.4.11 wants 3:1 for a control the user has
to find, and `--rule` misses at 2.78:1), the copied state is `--ok` as both icon
colour and 2px ring, and a refused write is `--danger` the same way. Note that
`.copy-btn svg` deliberately sets no `display`: a flex item is blockified
already, and at (0,1,1) that declaration outranks `.copy-ico-done`'s (0,1,0)
`display: none` and paints the checkmark permanently beside the clipboard glyph.

Failure is handled rather than swallowed. `navigator.clipboard` is absent on an
insecure origin and Safari/Firefox can refuse a write outright, so a failed copy
selects the command's text — the visitor presses Ctrl+C, or Command+C on macOS,
which the announcement names rather than guesses at — and says so in the
block's `role="status"` region. That region exists because the button's
`aria-label` is its whole accessible name: the icon carries no text, and
rewriting the name of the control someone just pressed is not an announcement.

**The three `portal_base.html` pages get no copy button**, deliberately. They
load no JavaScript at all (see *Static-page styling*), and none of them shows a
command worth copying; adding one there would mean re-admitting a script to
those pages, which is a policy change rather than a tweak.

## Store badge rules

Both badges are **unmodified official assets** — `img/google-play-badge.png`
from Google's Partner Marketing Hub, `img/app-store-badge.svg` from Apple's
marketing resources (see *Regenerating assets*). Neither vendor permits
recolouring, cropping or re-rendering, so the files are committed byte-for-byte;
the Apple SVG is not even whitespace-minified, which would have saved 380 bytes.
Each must also stay no smaller than the other.

**They are not directly comparable at the same box height.** Google's asset
bakes its own clear space into the canvas — 41 px on every side of 646×250, so
the visible lockup is 564×168 — while Apple's SVG (119.66×40) has none. Sizing
both boxes alike renders the Apple lockup 1.49× taller, which reads as Apple
being favoured rather than as a mistake. `site.css` therefore matches the
*lockups*: the Play badge renders at `width: 194px`, making its lockup
`194 × 168/646 = 50px` tall, and `.badge-link.badge-apple img` pins the Apple
badge to `height: 50px`. Re-derive that number if either width changes.

The same asymmetry misaligns the two lockups on the left once `.cta` wraps them
into a column, so `.badge-link.badge-apple` carries `padding: 12px` — Google's
inset at render size (`194 × 41/646`), reproduced rather than cropped away. Do
not close the remaining gap from the other direction: that is Google's mandated
clear space doing its job, and Apple asks for clear space of its own.

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

Five of these files are **mirrored byte-for-byte into `web/public/`** — the three
brand images (`wordmark.png`, `favicon.ico`, `apple-touch-icon.png`) **and both
font files from step 6** (`DepartureMono-Regular.woff2` and its OFL licence).
The SPA is a separate origin with a separate deploy (`deploy_web.py`) and no
shared asset pipeline, but it renders the same brand mark, tab icon and typeface,
so a visitor crossing from the apex sees no seam. Regenerating **any** of the
five means copying the new file over its `web/public/` twin in the same commit —
the font included, and note that step 6 ships it under an unchanged filename, so
nothing else would signal the drift. `web/src/app/brandAssets.test.ts` holds the
canonical list and fails Web CI if any copy diverges.

The two store badges are **not** generated here — they are vendor artwork,
downloaded and committed as-is. Refresh the Apple one from
`https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg`
and the Google one from the Partner Marketing Hub; see *Store badge rules* for
what may not be done to either.

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
