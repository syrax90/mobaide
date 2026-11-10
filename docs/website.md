# Marketing website (mobaide.com)

The public landing page at the apex domain. It exists because directories,
aggregators and social cards scrape the apex and its Open Graph tags — before
this page, `https://mobaide.com/` answered `{"detail":"Not Found"}`.

It is served by `central_server`, not by a separate static host, so it ships and
deploys with the relay and needs no extra infrastructure. A second marketing
page, the voice input feature page at `/voice-input`, is served the same way —
see *The voice input page* below.

**On this page**

- [Where things live](#where-things-live)
- [Changing the copy](#changing-the-copy)
- [Response headers](#response-headers)
- [The consent-gated Google Ads tag](#the-consent-gated-google-ads-tag)
- [Canonical origin](#canonical-origin)
- [The browser-client origin](#the-browser-client-origin)
- [Accessibility invariants](#accessibility-invariants)
- [Static-page styling](#static-page-styling)
- [The voice input page](#the-voice-input-page)
- [Screenshots and the platform switch](#screenshots-and-the-platform-switch)
- [Copyable command blocks](#copyable-command-blocks)
- [Store badge rules](#store-badge-rules)
- [Regenerating assets](#regenerating-assets)
- [Deployment](#deployment)

**This doc also governs the design system itself, not just the landing page.**
`site.css` is shared by the apex page *and* the three static pages under
`templates/portal_base.html` (`/privacy.html`, `/portal/delete-account-info`,
`/portal/cancel-deletion`) — see *Static-page styling* below.

The internal admin dashboard (`/dashboard/**`, `templates/base.html`) is a
separate light Bootstrap-flavoured sheet (`static/style.css`) and is deliberately
not part of this system.

**A third consumer mirrors the tokens: the web SPA** (`web/src/styles/theme.css`,
see the Theming bullet in [`docs/web-client.md`](web-client.md)). Its dark theme
copies this sheet's `:root` values verbatim — **a token change in `site.css` must
be mirrored there** — and its light theme is a derived variant that exists only in
the SPA. The SPA ships its own copy of the Departure Mono woff2 + OFL under
`web/public/fonts/` (different origin, same-origin CSP — it cannot load this host's
copy).

**A fourth is the iOS app** (`ios/Mobaide/UI/Theme/Theme.swift`, see
*Theme / design system* in [`docs/ios-client.md`](ios-client.md)). Its
`MobaideColorScheme.dark` / `.light` copy both of the SPA's blocks hex for hex, so
a token change here or in `theme.css` must be mirrored there too.

It ships
Departure Mono as the upstream OTF (iOS cannot register a woff2) and a byte copy of
the wordmark at 1x.

Two invariants restate per theme: the brand purples stay
fill/border-only **on dark surfaces** (on the SPA's light theme `#6008ac` doubles as
the accent *text* colour), and the light `--danger`/`--ok` are `#8c1d18`/`#116a2e`
because this sheet's `#f2b8b5`/`#23d18b` are light-on-dark colours that fail on
white.

## Where things live

| Path | What |
|---|---|
| `central_server/central_server/marketing/router.py` | Routes: `GET /`, `/voice-input`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`. Owns the response headers. |
| `central_server/central_server/marketing/loader.py` | Renders both pages + crawler files once at startup. |
| `central_server/templates/marketing_landing.html` | **All landing-page copy.** Edit here. |
| `central_server/templates/marketing_voice_input.html` | **All `/voice-input` copy.** |
| `central_server/templates/_site_foot.html` | Footer shared by both marketing pages (nav, trademark disclaimer). |
| `central_server/static/site/site.css` | The "modern pixel" design system — **shared by the landing page and the `portal_base.html` pages.** Owns every token, the `@font-face`, the pixel kit, `.btn`, `.card` and the `.site-head`/`.site-foot` chrome. |
| `central_server/static/site/portal.css` | Additions `site.css` lacks (the landing page has no forms): inputs, labels, radios, alerts, tables, choice fieldsets, legal-doc layout. Loaded **after** `site.css`. Declares no tokens and no `@font-face`. |
| `central_server/templates/portal_base.html` | Shell for the static pages — loads both sheets, carries the shared chrome markup, and loads **no script at all**. |
| `central_server/static/style.css` | **Admin dashboard only.** Light sheet (`--primary: #0d6efd`, `--radius: 6px`), not part of this design system. |
| `central_server/static/site/site.js` | Reveal-on-scroll, the `/voice-input` hero typewriter, copy buttons, the Android/iOS screenshot switch, and the cookie-consent banner with its post-consent Google Ads gtag.js loader. The two marketing pages only — `portal_base.html` does not load it. |
| `central_server/static/site/img/`, `fonts/` | Assets — see *Regenerating assets*. |
| `README.md` (repo root) | Embeds `og.png`, both store badges, five `shot-android-*.webp` and `shot-web-voice.webp` by relative path. Rename or remove one of those images and the README breaks. Its voice input numbers and section links mirror `/voice-input`, so change them together. |

The page is **pre-rendered once in `create_app()`**, so **a template or copy change
needs a server restart** to appear. CSS, JS and images are served from disk and do
not.

## Changing the copy

Edit `marketing_landing.html`. Two constraints, both enforced by
`test_marketing.py` — if a test fails after a copy edit, the test is probably
right:

**1. No inline `style="…"` and no inline `<script>`.** The response carries
`Content-Security-Policy: default-src 'self'` with no `'unsafe-inline'`, so
either would be silently blocked. Icons go in as inline `<svg>` markup rather
than `data:` URIs for the same reason. A `<script type="application/ld+json">`
block is fine — CSP does not treat a data block as executable script.

**2. Accuracy gate.** It also applies to the repository's `README.md`, which no
test checks. Several claims in the Play Store listing are contradicted
by the code. The store copy is deliberately left as-is (out of scope for
CLA-170), but the website must not repeat them:

| Do not write | Why | Write instead |
|---|---|---|
| "end-to-end" (auth or encryption) | Transport is TLS per hop; the relay terminates TLS and forwards plaintext through in-memory routing. There is no E2E crypto anywhere in the repo. | "Going through the relay — the default — every hop is TLS-encrypted (WSS)", and name the two auth mechanisms separately. **Both halves need the qualifiers in the "JWT auth on every hop" and unqualified "every hop is TLS-encrypted" rows** — do not write a bare "every hop is TLS-encrypted" or "JWT auth on every hop". |
| "32 sessions **per agent type**", or 32 as a hard limit | `max_sessions` (`mobaide_agent/mobaide/core/session_manager.py`) is one global cap over a flat session dict, and it is configurable (`mobaide/agent/config.py`); 32 is the default. | "Up to 32 concurrent sessions on one agent" |
| "**each** session in its own dedicated Git worktree" | Worktrees are opt-in per launch and Git-only. A `worktree=true` request against a non-Git project logs a warning and falls back to the project root. | The worktree claim **with both qualifiers**: opt-in per launch, Git projects only |
| any implication that nothing is stored server-side | The relay persists accounts, paired devices, prompt templates, saved shortcuts and custom agent types. | "the relay never persists session traffic" + name what it does persist |
| "no inbound ports", "nothing listens on your machine" | The agent always starts a listener, however the app reaches it — on `0.0.0.0:8000` by default, or wherever `HOST`/`PORT` point (`mobaide_agent/mobaide/agent/config.py:51`, passed to uvicorn in `mobaide/__main__.py`). | "No public IP or port forwarding", "nothing has to be reachable from the internet" |
| "10,000-line scrollback" replays on reconnect | Two different buffers. The realtime WS path replays byte-exactly from a ring capped at `RING_MAX_BYTES` (4 MiB, `core/session.py`); the 10,000 figure is `scrollback_size`, a deque of read **chunks** that backs the REST `/scrollback` endpoint (see `get_scrollback`'s docstring). | "a rolling buffer of recent output" — state the guarantee, not a line count |
| "JWT auth on every hop" | Only the app→relay hop is a JWT. Each agent presents an opaque per-agent token verified by SHA-256 `compare_digest` (`auth/service.py` → `db.verify_agent_token`). | name the two mechanisms separately |
| "every hop is TLS-encrypted" **unqualified** | True for the relay path, which is the only one a client can take. See the note below the table. | scope it ("going through the relay, every hop is…") — without describing a direct mode, see the "direct"/"LAN" row |
| "no analytics SDKs in the app" (any absolute no-analytics / no-tracking claim about the mobile apps) | Since Phase 2 of the Google Ads rollout both mobile apps carry Google Analytics for Firebase for install-conversion measurement (docs/app-conversion-measurement.md); the privacy policy's Section 3.8 describes it. The *agent* still has none. | "The agent carries no analytics SDK; the mobile apps carry Google's Firebase Analytics for one purpose — telling Google Ads which installs went on to pair an agent" — keep the agent claim, name the single purpose, and say it never sees sessions |
| a "direct"/"LAN" mode, or "point the app straight at the agent" | No client exposes one. The mode is code, not a feature. See the note below the table. | say nothing about it; describe only the relay path |

**Notes on two of the rows above:**

- **"every hop is TLS-encrypted" unqualified.** The DIRECT-mode plumbing the clients
  still carry would build `ws://` unless the agent sits behind HTTPS —
  `WebSocketManager.kt`: `if (agentUrl.startsWith("https")) "wss" else "ws"` — and
  the agent's own listener is plain HTTP by default.
- **A "direct"/"LAN" mode.** `AuthRepository.connectAgent()` (Android and iOS) has
  no UI caller — the Connections screen's "+" routes to the relay login. The agent's
  `UserStore.add_user()` has no CLI verb or endpoint, so the user store
  `POST /api/auth/token` checks is always empty.

The worktree capability itself is broad and true, and the page says so: every
session kind supports it — Claude Code, Codex CLI, Antigravity CLI, raw
Terminal, and custom agent types.

Spell the products **Claude Code**, **Codex CLI**, **Antigravity CLI**.

## Response headers

`marketing/router.py` sets only `Cache-Control: public, max-age=300` on `/` and the
two crawler/favicon TTLs. Everything else is set site-wide by
`central_server/central_server/security_headers.py`. `/voice-input`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico` and
`/privacy.html` get the strict site policy:

```
Content-Security-Policy: default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin-allow-popups
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), identity-credentials-get=()
```

The landing page (`/`) alone takes `_LANDING_CSP` instead: the same policy
widened with `script-src`, `img-src`, `connect-src` and `frame-src` lists naming
the origins [Google's CSP guide](https://developers.google.com/tag-platform/security/guides/csp)
documents for Ads conversion tracking — see "The consent-gated Google Ads tag"
below for why that widening exists and what keeps it honest.

Values and per-surface variants of these headers live under "Security response
headers" in [`docs/deployment.md`](deployment.md).

### Neither CSP may be loosened

**Neither CSP may be loosened further**, and on the four marketing paths the
policy is the one thing `CSP_REPORT_ONLY` cannot de-enforce: the crawler files
already ran theirs enforced in production, and the landing policy is a strict
superset of one that did. `/privacy.html` shares the strict policy but not that
exemption: it soaks like any other new surface.

The rendered landing page satisfies the strict site policy with no relaxation at
all: its CSS and JS are external same-origin files, its icons are inline `<svg>`
markup rather than `data:` URIs, its font is same-origin, and the JSON-LD block is
a data block CSP does not treat as executable script.

The Google origins exist only
for what `site.js` injects after the visitor accepts the cookie banner. The HTML
itself must never reference them; `test_marketing.TestConsentBanner` fails the build
if it does. The tests in `central_server/tests/test_marketing.py` pin the exact
header values and scan the rendered page for inline `<script>` and `style=`, which
stops a copy edit from quietly needing `'unsafe-inline'`.

The `portal_base.html` pages and the dashboard get their own policy — see the
per-surface table under "Security response headers" in
[`docs/deployment.md`](deployment.md). No template in this repo contains an inline
`<script>`, an `onclick=` or an `hx-on:`: the dashboard's behaviour lives in
`static/dashboard.js`, and the three static pages need no behaviour at all.

## The consent-gated Google Ads tag

The landing page carries a Google Ads conversion tag (`AW-18403068615`), gated
behind a cookie-consent banner because the site is EU-hosted and the privacy
policy promises no un-consented tracking:

- The banner is static markup at the bottom of `marketing_landing.html`, shipped
  with the `hidden` attribute. `site.js` reveals it only when
  `localStorage["mobaide-ads-consent"]` holds no choice, so a no-JS visitor — who
  can never load the tag — is never asked, and a returning visitor is not asked
  twice.
- The whole flow runs only when `location.hostname` is exactly `mobaide.com`: the
  assets ship identically to every environment, and an Accept on localhost or a
  staging deploy would otherwise feed that environment's signals into the live Ads
  property. To verify locally, launch Chrome with
  `--host-resolver-rules="MAP mobaide.com 127.0.0.1"` and browse
  `http://mobaide.com:<port>/` (`location.hostname` never carries the port).
- **Accept** stores `granted` and injects
  `https://www.googletagmanager.com/gtag/js?id=AW-18403068615` with a Consent
  Mode v2 default-denied → update-granted pair queued ahead of it, followed by
  `config` and the `conversion` event that counts the "Page view" conversion
  action (`send_to: AW-18403068615/<label>`, `ADS_PAGE_VIEW_LABEL` in `site.js`).
  - The label is what makes the base tag measure anything: `config` alone pings the
    tag but counts no labelled action, and Google Ads reports that action as "never
    received a tag ping". The event must stay queued *after* `config`, because gtag
    resolves `send_to` against the configured tag.
  - Only `ad_storage` and `ad_user_data` are ever granted. `ad_personalization`
    stays denied: the banner promises measurement, and granting it makes gtag fire
    pings at per-country `www.google.<tld>` remarketing endpoints no static CSP can
    enumerate (verified live: a CSP-blocked `www.google.cz/pagead/1p-user-list`
    ping). `analytics_storage` stays denied because this is an Ads tag, not an
    analytics rollout.
  - Google's "one tag per page" rule is enforced by an injected-once flag.
- **Decline** stores `denied` and nothing ever loads.
- **Withdrawal is one click too** (GDPR Art. 7(3)): the footer's "Cookie settings"
  button — hidden until `site.js` arms the flow, same `can-copy` contract as the
  copy buttons — reopens the banner regardless of the stored choice. Declining after
  a prior Accept pushes `consent update: denied` to a tag already running and
  expires every `_gcl_*` cookie (both domain spellings — gtag registers them on the
  registrable domain).
- **Accept also asks the server for our own `mobaide_ads` cookie.** `site.js` never
  writes it: a domain cookie set from script proves nothing, since any host on the
  registrable domain (the GitHub-Pages `get.mobaide.com` included) could plant one.
  - **Request.** The page POSTs the landing click id (`gclid`/`wbraid`/`gbraid`,
    when the landing URL carried one) and the page-load time to the same-origin
    `POST /api/ads/consent` (`central_server/ads/router.py`). It answers with a
    `Set-Cookie` for `Domain=<host>`, `Path=/`, 90 days, `SameSite=Lax`, `Secure`
    per `USER_SESSION_COOKIE_SECURE`.
  - **Value.** `<base64url(payload)>.<hmac>`, signed with a key derived from
    `JWT_SECRET_KEY`. The payload `v=1&k=<kind>&t=<epoch s>&id=<id>&n=<network>`
    omits `k`/`id` on a bare consent marker. `t` is the arrival time capped at the
    server clock, so neither a tab left open before Accept nor a skewed browser
    clock can post-date a click. `n` is a keyed fingerprint of the network the
    Accept came from, which binds the consent to the visitor at sign-up (see
    docs/deployment.md).
  - **Decline.** If a Decline lands while the request is in flight, the response
    handler clears the cookie again. Decline first POSTs the cookie value to
    `/api/ads/consent/withdraw` — the server drops the stored attribution from every
    account created with that cookie — and only then expires the cookie locally.
  - **Origin check.** The endpoint accepts same-origin requests only
    (`Sec-Fetch-Site` / `Origin`), so a cross-site POST cannot mint a cookie for a
    victim.
  - **Renewal.** An existing cookie is requested anew only when the visit carries a
    new click id: a plain return visit never renews the disclosed 90-day lifetime,
    and a stored click id is never downgraded to a marker. The check runs on Accept
    and on every stored-`granted` page load.
  - **Reader.** The only reader is the web SPA (`web/src/auth/adAttribution.ts`),
    which forwards it as the optional `ad_attribution` field of the sign-up
    requests; the server verifies the signature before storing anything. See *Google
    Ads conversion upload* in docs/deployment.md.
- Because the tag is injected at runtime, the served HTML stays same-origin and
  inline-free. `TestConsentBanner` adds the reverse guard: the HTML must never
  reference `googletagmanager` itself, or the consent gate is bypassed.
- The only server-side accommodation is `_LANDING_CSP` in `security_headers.py`,
  scoped to `/` alone; `/privacy.html` and the crawler files keep the strict policy.
- The banner's disclosure lives in the privacy policy's Section 3.5 (the banner
  links it). `central_server/legal/privacy.md` must stay in sync with what the tag
  actually does: cookies only after Accept, choice stored in local storage,
  withdrawal by clearing site data, the `mobaide_ads` cookie and the one-time
  first-pairing report. The mobile apps' Firebase lane has its own disclosure in
  Section 3.8 and the same sync rule against docs/app-conversion-measurement.md.

### Changing or removing the tag

Changing the tag id means changing `ADS_TAG_ID` in `site.js`. A new or renamed
conversion action also means a new `ADS_PAGE_VIEW_LABEL`.

Removing the tag means removing all of these together; each is load-bearing for the
others: the banner, the `site.js` module, the `_LANDING_CSP` bucket, the
`mobaide_ads` cookie writer together with `web/src/auth/adAttribution.ts` and the
`ad_attribution` request field, and the privacy-policy passages.

### robots.txt rules

`/robots.txt` disallows only `/api/` and `/portal/`, with one carve-out: an
`Allow: /portal/delete-account-info` line **emitted before the disallows**. That
page is anonymous, is the URL submitted to Google Play Console's Data Safety form,
and is linked from this site's own footer, so the blanket `/portal/` block must not
swallow it (RFC 9309 §2.2.2 longest-match makes the specific rule win; Google and
Bing honour it). `/portal/cancel-deletion` stays blocked on purpose: it carries a
single-use token in its query string.

It deliberately does **not** list `/dashboard/`, `/docs`, `/redoc` or
`/openapi.json`: nothing public links to them, so a `Disallow` buys no crawl budget,
and naming them would publish the admin login's location and undercut
`app._require_docs_admin`, which answers 404 on every rejection so a probe cannot
tell docs-enabled-but-gated from docs-disabled. The same `robots.txt` is served at
`api.mobaide.com` (both hosts share one Caddy site block). If those paths ever need
a noindex guarantee, use an `X-Robots-Tag: noindex` response header, not a public
file.

### Static asset caching

`CachedStaticFiles` supplies the `Cache-Control` that Starlette's `StaticFiles`
omits entirely — without it every asset costs a revalidation round-trip. Four
tiers:

| Path | Value | Why |
|---|---|---|
| `static/htmx.min.js` | `no-cache` | **Do not raise this.** `templates/base.html` pins an SRI `integrity` hash on it and the filename carries no version. |
| `static/dashboard.js` | `no-cache` | **Do not raise this either**, for an unrelated reason: it contracts with the dashboard templates through `data-` attributes (`data-user-id`, `data-no-row-toggle`, `data-confirm-target`, `data-modal-close`) and the filename carries no version. |
| `static/site/**` | 7 days | Versioned by a `?v=` query the templates append, so an asset edit busts them immediately. Covers `portal.css` too — which is why it lives here rather than beside `style.css`. |
| `static/style.css` (admin dashboard) | 1 hour | Referenced without a version query, so the TTL is what propagates a deploy. |

#### Why the two `no-cache` entries stay

- **`static/htmx.min.js`.** If a client may reuse a cached copy without
  revalidating, an htmx upgrade hands it new HTML plus stale JS. The hashes
  disagree, and the browser blocks htmx — every dashboard form silently breaks until
  the entry expires. `no-cache` still stores and still 304s; it only forbids reuse
  without asking.
- **`static/dashboard.js`.** Rename one `data-` attribute on both sides and an admin
  holding a cached copy gets new markup plus stale JS: rows stop expanding, modals
  stop dismissing, the delete-user confirm gate stops arming, with no server-side
  symptom. `site/site.js` needs no entry: the tree-wide `?v=` hash busts it.

### The `?v=` asset version

That `?v=` value is a **content hash over the whole `static/site/` tree**
(`marketing.loader.asset_version`), not the package version. `deploy_central.py`
ships whatever is in the working tree, while `__version__` only moves when
release-please bumps it, so two deploys can carry different assets under one version
and a repeat visitor would pair fresh HTML (5-minute TTL) with week-stale CSS or
images. If the tree is missing the helper degrades to the package version rather than
failing startup.

It hashes the whole tree, not an enumerated file list: an over-broad
hash only busts a few extra URLs, whereas a list that drifts out of sync with the
template silently reintroduces the stale bug.

It is computed once in `create_app()` and hoisted to `app.state.asset_version`,
because **three** render paths stamp it: `marketing.loader.render_landing` at
startup, `portal/router.py::_portal_context`, and `legal/router.py`. All three must
stamp the identical query. A divergent one re-keys the cache entries per surface, so
a landing → `/privacy.html` navigation refetches a 7-day-cached `site.css` and
wordmark the browser already holds.

The two routers use `Jinja2Templates`' permissive
default `Undefined` (unlike `marketing/loader.py`, which uses `StrictUndefined`), so a
forgotten context key degrades to a bare `?v=` **silently**; `test_portal_assets.py`
is the guard.

Three `static/site/` assets carry **no** `?v=` query, each deliberately:

- **The font**, in **all three** places it appears: the `@font-face` `url()` in
  `site.css`, the `<link rel="preload">` in `marketing_landing.html`, and the
  `<link rel="preload">` in `portal_base.html`. Preload matching keys on the full URL
  including the query string, and a static stylesheet cannot carry a template
  variable, so a `?v=` on a preload alone would download the 22.5 KB font twice. Keep
  all three URLs byte-identical and rename the file if it is replaced.
- **`og.png`**, referenced only from `og:image` / `twitter:image` / JSON-LD. Third-party
  scrapers fetch it and cache OG data in their own scrape caches independently of
  `Cache-Control`, so a query string would only re-key a 144 KB image on every deploy
  without refreshing a stale card. Swap artwork with a platform re-scrape tool.
- **`favicon.ico`**, which is also served from the root `/favicon.ico` route.

## Canonical origin

`config.site_base_url` (`SITE_BASE_URL`, default `https://mobaide.com`) is the
origin used for the canonical link, OG/Twitter image URLs, JSON-LD and the
sitemap. It is deliberately **not** `public_base_url`: that is the *relay* origin
embedded in QR pairing payloads, which production points at `api.mobaide.com`, and
reusing it would publish the API host as the site's canonical URL.

The value is shape-checked (`scheme://host[:port]`, no path) and a malformed one
falls back to the default rather than raising — a typo in a marketing URL should
not take the relay down. A trailing slash is stripped in both places.

## The browser-client origin

`config.web_app_base_url` (`WEB_APP_BASE_URL`, default `https://app.mobaide.com`)
is a **third** origin, and it is the one every account link on this host resolves
against: the header and footer "Sign in" buttons, the hero "Open the web app" CTA,
the FAQ entry that names the browser client, and the cross-links in the two
surviving `/portal/*` pages. It is hoisted to `app.state.web_app_origin` alongside
`asset_version`: the landing pre-render, `portal/router.py::_portal_context` and
`legal/router.py` all stamp it, and they must all name the same host.

Set it to a Vite dev server (`WEB_APP_BASE_URL=http://localhost:5173`) to run the
whole marketing site against a local SPA. Same shape check and fall-back as
`SITE_BASE_URL`.

The `StrictUndefined`-vs-`Undefined` asymmetry applies as for `?v=`: the landing
page raises on a missing `app_origin`, while the two `Jinja2Templates` routers would
render `href="/login"` — a same-origin path that does not exist on this host — so
both are asserted in `test_marketing.py` and `test_portal.py`.

## Accessibility invariants

- **`--brand` (`#6008AC`) and `--brand-2` (`#6650A4`) are fill and border
  colours only.** `#6650A4` on `#1E1E1E` is ~2.6:1 and fails WCAG AA. Text uses
  `--accent` / `--text` / `--text-muted`; the worst text pair on the page is
  4.6:1. Re-check any new pair before merging. The `portal.css` components keep the
  purples structural: the checked choice-row ring, the radio's checked fill, and
  `.h-pixel`'s underline bar.
- **Form-control boundaries use `--gray` (`#8B8B8B`, 5.26:1 on `--bg-2`), not
  `--rule` (`#625B71`, 2.78:1).** WCAG 1.4.11 wants 3:1 for the boundary of a UI
  component the user has to find, and `--rule` misses it. `--rule` remains correct
  for decorative frames and plates (`.px-frame`, `.chip`, `.alert`).
- **`--danger` (`#F2B8B5`, 10.5:1) and `--ok` (`#23D18B`, 9.0:1)**, both measured
  on `--bg-2`, are used as text *and* as 2px ring fills. They are light-on-dark on
  purpose: a dark `#8C1D18` error container measures 1.83:1 as a ring on
  `--surface` and would fail 1.4.11.
- The wordmark keeps its light plate because it is dark purple ink drawn for a
  light background (lifted from the app icon).
- All motion is gated on `prefers-reduced-motion: reduce`.
- Animation start-states are gated on a `js` class that `site.js` sets from
  `<head>`, and the `/voice-input` hero transcript's hidden state self-releases on every exit
  path (finished, reduced-motion, no `IntersectionObserver`, or a 2.5s
  watchdog). **Never add a hidden-by-default state that only a successful
  animation can undo.**

## Static-page styling

Three server-rendered pages extend `templates/portal_base.html` — `/privacy.html`,
`/portal/delete-account-info` and `/portal/cancel-deletion` — and render in this
same design system, so a visitor crossing from the landing page sees no seam.

They are what is left of the end-user portal. Sign-in, sign-up, verification,
password reset and the account page moved to the browser client at
`app.mobaide.com`; the two `/portal/*` pages stayed because external systems
name them (the deletion email links one, Google Play Console's Data Safety form
links the other). The shell's "Sign in" button and every cross-link in their copy
resolve against `{{ app_origin }}`, which comes from `WEB_APP_BASE_URL` — see
*The browser-client origin* above.

**Two sheets, in this order:** `site.css`, then `portal.css` (only what `site.css`
lacks — see *Where things live*).

**Placement rule:** a rule any dark Mobaide surface could want goes in `site.css`;
a rule only a form or document page wants goes in `portal.css`. `portal.css`
declares no `:root` block and no `@font-face` — duplicating either is the drift
that reusing `site.css` exists to prevent. `test_portal_assets.py` enforces that,
the no-non-zero-`border-radius` rule, the brand-as-fill-only rule, and the
`--gray` form-boundary rule.

`portal.css` still carries rules for components no surviving template uses (the
auth divider, the password reveal, the modal layer). They are kept rather than
pruned: the sheet is shared with `/privacy.html`, dead selectors cost nothing at
runtime (about 10.5 KB raw, ~3 KB gzipped, once per client per deploy), and the
SPA's own stylesheet was derived from these. Do not prune during an unrelated
change.

**Button vocabulary.** In `style.css` bare `.btn` is the *secondary* outline
button; in `site.css` it is the *filled brand* one. These pages use:

| Class | Role |
|---|---|
| `.btn` | Primary action (Open the web app, Yes cancel deletion, Sign in) |
| `.btn btn-ghost` | Secondary / Cancel / Dismiss / No |
| `.btn btn-danger` | Destructive |
| `.btn btn-sm` | Small mono chrome action |

There is no `.btn-primary` — reintroducing it would silently render an unstyled
button.

**No JavaScript reaches these pages at all.**

- `site.js` is deliberately not loaded: its first act is to set a `js` class that
  arms `site.css`'s `opacity: 0` animation start-states, after which no `site.css`
  motion rule could ever un-hide the content. Nothing here uses `[data-reveal]` or
  `[data-typewriter]`.
- Neither is htmx, nor any behaviour bundle, nor a sign-in SDK. The remaining
  interactivity is one native form POST.
- Adding a `<script>` back is a policy change, not a tweak: `security_headers.py`
  serves these pages a CSP that names no third-party origin and grants no
  `'unsafe-inline'`.

**The admin dashboard** still loads htmx and `static/dashboard.js`, and sets
`includeIndicatorStyles: false` in its `htmx-config` meta. htmx would otherwise
inject an inline `<style>` block at init that forces `'unsafe-inline'` into
`style-src`. The three rules it would have written live in `style.css`; delete
them and any future `hx-indicator` is silently dead.

`.badge*`, `.info-grid`, `.qr-*`, `.token-display`, `.search-bar`, `.user-row` and
`.detail-row` are intentionally **not** ported; they stay in `static/style.css`.

## The voice input page

`/voice-input` explains voice input (dictation, GH #736/#737 — see
[`docs/voice-input.md`](voice-input.md)) to visitors. The landing page points at it
from the header nav, a highlighted first feature card, the "What leaves my machine?"
answer and the shared footer; the sitemap lists it.

- **Strict CSP, no consent banner.** It is in `_SITE_PATHS` (soakable, like
  `/privacy.html`), not in the `/`-only `_LANDING_CSP` bucket. It loads
  `site.css` and `site.js` but ships no `[data-consent-banner]`, so
  `setUpConsentBanner()` returns before it could inject gtag (which the strict
  policy would block anyway). The shared footer renders the "Cookie settings"
  control only when the including page sets `with_consent_settings` (the landing
  page does).
- **Same copy rules as the landing page.** No inline style or script, no
  cross-origin subresource, none of the accuracy-table claims (no direct mode
  — the clients' direct-connection dictation path is not described), and the
  TLS claim scoped with "going through the relay". `TestVoiceInputPage` in
  `test_marketing.py` pins all of it, plus the voice facts the page states.
- **Where the numbers come from.** Change the page when any of these changes:

  | Source | Supplies |
  |---|---|
  | `mobaide_agent/mobaide/voice/catalog.py` | Languages, download size, ~700 MB while transcribing |
  | `host.py` | The 12 GB / 4 cores / 2 GB recommendation and the unsupported cases |
  | `dictation.py` | The two-minute cap |
  | `helper.py` | The three-minute idle exit |
  | `cleanup.py` | The opt-in transcript cleanup through the session's AI agent, stated in *Where your voice goes* and the FAQ |
  | *Voice input* section of [`docs/deployment.md`](deployment.md) | Disk, download hosts, the CLI |

- **The hero is a drawn mock** (`.voice-demo`, `.dict-*` in `site.css`, inside one
  `role="img"`) showing what no single capture can: recording, then the transcript
  waiting in the command field. Its recording bar copies the apps' (cancel, elapsed
  time, level meter, stop), so keep the two in step. The transcript uses the
  `site.js` typewriter (`[data-typewriter]`, one per page) and its watchdogs;
  the level meter and recording dot stop under `prefers-reduced-motion`.
- **Three captures, two frames.** `#screens` shows `shot-android-voice.webp` and
  `shot-ios-voice.webp` in the `.device` phone frame, then `shot-web-voice.webp` in
  `.browser` (a drawn title bar with `app.mobaide.com`, capped at 880px to stay in
  proportion with the phones). All three are bare captures.

## Screenshots and the platform switch

The landing hero is a capture too: `shot-web-chat.webp`, the web app's chat view,
in the same `.browser` frame as the `/voice-input` web capture. It is above the
fold, so it is not `loading="lazy"`.

`#screens` carries **two panels of nine captures each**, Android and iOS, behind an
`aria` tab pair. The phone body around every capture is drawn by `.device` in CSS, so
the files must stay **bare app captures** — a screenshot with a frame already baked in
double-frames.

**The switch degrades to both panels.** The markup ships with the tablist at
`display: none` and *neither* panel `hidden`, so the page shows all eighteen
screenshots under their `On Android` / `On iOS` headings when scripting is off,
`site.js` fails to load, or `setUpShotSwitch()` returns early because an id does not
resolve. `setUpShotSwitch()` adds `is-live` to `[data-shots-switch]` only after both
tabs are wired; that class reveals the tablist and hides the redundant headings. This
is the `can-copy` pattern: never a hidden state that only a *successful* run can undo.

- **Captions are platform-neutral**, so the tab is the only thing naming the platform.
  The `alt` text is not: it names the platform and the per-platform control
  differences (Android's *command field* vs iOS's *message field*, iOS's extra "Pair
  with a code" affordance).
- **Tabs use automatic activation** (Left/Right move *and* select), cheap because the
  hidden panel's images are `loading="lazy"` and never fetched. **Up/Down are
  deliberately not handled:** the tablist is horizontal, and claiming the vertical
  pair would `preventDefault()` a scroll keypress while a tab held focus.
- **The default panel follows the user agent:** iOS on an iPhone or iPad, Android
  otherwise. An iPad is not detectable from the UA alone (since iPadOS 13 Safari
  reports a `Macintosh` UA), so a `Macintosh` claim is confirmed against
  `navigator.maxTouchPoints`; no Mac has a touchscreen, so there is no false positive.
- **`.shots-tab` colours.** The boundary is `--gray`, not `--rule`, for the same WCAG
  1.4.11 reason as `.copy-btn`. The selected tab is a `--brand` fill with `#fff` on
  it, but its **ring is `--accent`, not `--brand-2`**: 1.4.11 wants 3:1 for whatever
  identifies a *state*, and `--brand-2` gives 1.53:1 against the fill and 2.90:1
  against the page, where `--accent` gives 5.79:1 and 10.99:1. Because the ring
  carries the state, `.shots-tab:hover` may only brighten the label.

## Copyable command blocks

The two install one-liners in step 01 each sit in their own `.copyblock`: a label
row with an icon-only copy button, over the framed `<pre class="code">` holding
the command. Android's `InstallCommandCard` is the closest mirror in the clients.

- **The command exists in exactly one place: the `<code>` element.** `site.js`
  reads `code.textContent` at click time; the button carries a bare `data-copy`
  hook with no value. A `data-copy="curl …"` would be a second copy that can drift
  from what the visitor reads.
- **The buttons are `display: none` until `site.js` sets `can-copy`** on the root
  element, after the Clipboard API checks out and every button is wired. Do not gate
  on the `js` class: `navigator.clipboard` is undefined on an insecure origin, so
  `js` alone would show a button that does nothing. The command underneath is
  selectable text either way.
  - This is not the hidden-by-default state the accessibility invariants forbid:
    `can-copy` is set synchronously from `init()`, and no animation is in the path.
  - `setUpCopyButtons()` runs **before** `init()`'s reduced-motion early return,
    since copying is not motion.
- **Colours** follow the invariants above: boundary `--gray` (WCAG 1.4.11), copied
  state `--ok` as icon colour and 2px ring, refused write `--danger`.
  `.copy-btn svg` deliberately sets no `display`: at (0,1,1) it would outrank
  `.copy-ico-done`'s (0,1,0) `display: none` and paint the checkmark permanently
  beside the clipboard glyph.
- **A failed copy** (no `navigator.clipboard` on an insecure origin, or Safari/Firefox
  refusing the write) selects the command's text and says so in the block's
  `role="status"` region, naming Ctrl+C or Command+C on macOS. The region exists
  because the button's `aria-label` is its whole accessible name.
- **The three `portal_base.html` pages get no copy button.** They load no JavaScript
  (see *Static-page styling*); adding a script there is a policy change.

## Store badge rules

Both badges are **unmodified official assets** — `img/google-play-badge.png`
from Google's Partner Marketing Hub, `img/app-store-badge.svg` from Apple's
marketing resources (see *Regenerating assets*). Neither vendor permits
recolouring, cropping or re-rendering, so the files are committed byte-for-byte.
Each must also stay no smaller than the other.

**They are not directly comparable at the same box height.** Google's asset
bakes 41 px of clear space into every side of its 646×250 canvas (visible lockup
564×168); Apple's SVG (119.66×40) has none. Equal boxes render the Apple lockup
1.49× taller, which reads as favouring Apple.

- `site.css` matches the *lockups*: the Play badge renders at `width: 194px`
  (lockup `194 × 168/646 = 50px` tall) and `.badge-link.badge-apple img` pins the
  Apple badge to `height: 50px`. Re-derive that number if either width changes.
- `.badge-link.badge-apple` carries `padding: 12px` (Google's inset at render size,
  `194 × 41/646`) so the lockups align on the left once `.cta` wraps into a column.
  Do not close the remaining gap from the other direction: that is Google's
  mandated clear space, and Apple asks for clear space of its own.

## Regenerating assets

Sources live outside the repo in
`~/Documents/ML/Projects/mobaide-related/Google Play Console/`. The generator is
a one-off script, not committed; recreate it when assets need to change:

1. `python3 -m venv <tmp>/venv && <tmp>/venv/bin/pip install pillow numpy`
2. Wordmark: lift the pixel wordmark off the app icon's white plate.
   - Classify each pixel to its ink (`#6008AC` or `#8B8B8B`) by direction-from-white
     and take alpha from how far it travelled toward that ink.
   - Zero any pixel with luminance ≥ 235: the plate is a faint off-white gradient,
     so alpha thresholding alone cannot separate it from a light antialiased edge.
   - Crop to the bbox — 454×81 — then box-downscale by 2.
   - Ship one resolution only: `image-rendering: pixelated` wants a crisp
     nearest-neighbour upscale, so a 2x variant is heavier and smoother than intended.
3. Screenshots: Android in `Google Play Console/Screenshots/original/<design>/`, iOS
   in `IOS/app-store-connect/screenshots/original/<design>/`.
   - Take the **bare app captures**, **not** any `*_1080px.png` sibling: those are
     store marketing composites with a headline and phone frame baked in, which would
     double-frame against the CSS device frame.
   - Resize to 420px wide, WebP quality 72, name the output
     `shot-<android|ios>-<slug>.webp`, and record each file's encoded height (the
     template declares it).
   - The in-session capture is `inside-session-acp.png` (the chat view), shipped as
     `shot-<android|ios>-chat.webp`. The landing hero is the web app's
     `web-app/Screenshots/inside-session-acp.png`, shipped as `shot-web-chat.webp`
     at 1400px wide, quality 80, inside the same `.browser` frame as the voice page.
   - The `/voice-input` captures are the `voice-recognition.*` files in those two
     trees, plus the web app's `web-app/Screenshots/voice-recognition.png`, shipped
     as `shot-web-voice.webp` at 1600px wide, quality 80 so its small terminal text
     stays legible in the 880px frame.
4. Icons: `favicon.ico` multi-size 16/32/48; `apple-touch-icon.png` at 180px
   quantized to 64 colours.
5. OG card: 1200×630 **PNG or JPEG** (several scrapers reject WebP), composed
   from the wordmark on a light plate plus the Android chat screenshot
   (`inside-session-acp.png`) in a 10px `#625B71` frame, 430px inner height.
6. Font: `DepartureMono-Regular.woff2` from the
   [Departure Mono](https://github.com/rektdeckard/departure-mono) release,
   shipped whole at 22.5 KB with its OFL licence alongside (no subsetting step).

**Five of these files are mirrored byte-for-byte into `web/public/`:** the three
brand images (`wordmark.png`, `favicon.ico`, `apple-touch-icon.png`) and both font
files from step 6 (`DepartureMono-Regular.woff2` and its OFL licence). The SPA is a
separate origin with a separate deploy (`deploy_web.py`) and no shared asset
pipeline.

Regenerating **any** of the five means copying the new file over its
`web/public/` twin in the same commit — the font included, since step 6 keeps its
filename and nothing else would signal the drift. `web/src/app/brandAssets.test.ts`
holds the canonical list and fails Web CI if any copy diverges.

The two store badges are **not** generated here: they are vendor artwork, committed
as-is. Refresh the Apple one from
`https://developer.apple.com/assets/elements/badges/download-on-the-app-store.svg`
and the Google one from the Partner Marketing Hub; see *Store badge rules* for
what may not be done to either.

## Deployment

Manual, like the rest of the relay: an operator runs
`deploy/prod/deploy_central.py`. No workflow deploys the server.
**The apex 404 disappears only once that deploy runs.**

### The `www` redirect

Both `deploy/prod/Caddyfile.LetsEncrypt` and `Caddyfile.CloudflareOrigin` carry a
`www.mobaide.com` block that 308-redirects to the apex. Without it `www` matched no
site block and got Caddy's default empty 200 (zero-byte body, no `Via: 1.1 Caddy`
header).

`www` needs a DNS record either way. Under `LetsEncrypt` it must be "DNS only"
(gray cloud) so ACME HTTP-01 can complete; under `CloudflareOrigin` it is
proxied and served by the origin certificate, which must therefore carry
`www.mobaide.com` as an explicit SAN. Do **not** cover it with a `*.mobaide.com`
wildcard: that SAN also matches `app.mobaide.com`, which silently disables ACME
for the SPA host — see [TLS certificates](deployment.md#tls-certificates).

If CLA-167 (per-host Caddy site blocks) lands later, fold this block into its
structure rather than keeping a fourth top-level block.
