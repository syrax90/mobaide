# Deployment Guide

Mobaide has two separately deployable services:

- **Central server** (`central_server/`) — multi-tenant relay hub. Runs on one Linux host with a public DNS name (e.g. `mobaide.com`). The Android app and every agent connect to it.
- **Mobaide agent** (`mobaide_agent/`) — per-user-machine PTY runner. Runs natively on each user's own workstation and dials OUT to the central server over an authenticated WebSocket.

The **"Production (central server on a VPS)"** section below is the recommended path for the central server. The rest of this document covers per-machine agent deployment.

## Production (central server on a VPS)

Deploy the central server only to a remote Ubuntu host (e.g. `mobaide.com`) from your Windows or macOS workstation, with a single Python (3.12+) command — no full repo mirror on the VPS, no container registry.

### One-time setup

Follow [`deploy/prod/bootstrap-mobaide.md`](../deploy/prod/bootstrap-mobaide.md):

1. On the VPS: `mkdir /opt/mobaide`, populate `/opt/mobaide/.env` from `central_server/.env.example`, open ports 80/443.
2. DNS: `A` record for your domain → VPS public IP, plus a `www` record (both Caddyfiles 308-redirect `www` → apex). Cloudflare proxy can be on (use `CloudflareOrigin` TLS mode) or off (use `LetsEncrypt`). Add an `app` record for the browser SPA — **DNS only (gray cloud) in BOTH TLS modes**: orange cloud would put Cloudflare's ~100 s proxy idle timeout in the path of every relay WebSocket, and a gray-cloud host cannot serve the Cloudflare Origin cert (browsers do not trust the Origin CA), so Caddy ACME-issues a Let's Encrypt cert for `app.mobaide.com` in both modes.
3. `CloudflareOrigin` only: **issue the Origin Certificate for exactly `mobaide.com, www.mobaide.com, api.mobaide.com` — no wildcard.** The Cloudflare dialog pre-fills `mobaide.com, *.mobaide.com`; the wildcard must be deleted. Step 2's ACME issuance for `app.mobaide.com` happens *only if no loaded certificate matches that host*, and Caddy's certificate cache is global rather than per-site — so a `*.mobaide.com` SAN matches SNI `app.mobaide.com`, Caddy skips ACME for it entirely, and the SPA serves an Origin CA certificate that every browser rejects. See [TLS certificates](#tls-certificates).
4. On your workstation: add an `~/.ssh/config` entry so `ssh mobaide` works with key-based auth.

### Deploy

```bash
cd <repo-root>
python deploy/prod/deploy_central.py                              # LetsEncrypt (Cloudflare gray cloud)
python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin  # CF Origin Cert (orange cloud)
python deploy/prod/deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin   # classify a TLS failure, deploy nothing
python deploy/prod/deploy_central.py --skip-build                 # restart without source re-sync
python deploy/prod/deploy_central.py --rollback                   # swap back to previous version
python deploy/prod/deploy_central.py --force                      # allow dirty working tree
```

### What's on the server

```
/opt/mobaide/
├── docker-compose.prod.yml      # synced from deploy/prod/ each run
├── Caddyfile                    # copied from deploy/prod/Caddyfile.<TlsMode>
├── .env                         # secrets, chmod 600, NEVER synced
├── tls/                         # origin-cert.pem/key only for CloudflareOrigin mode
├── central_server/              # current source (synced from repo)
├── central_server.prev/         # previous source (kept for rollback)
├── www/                         # browser SPA, served by Caddy at app.mobaide.com
│   ├── releases/<tag>/          #   one dir per deploy_web.py release (newest 5 kept)
│   ├── current -> releases/...  #   RELATIVE symlink, atomically swapped per deploy
│   └── .previous                #   release live before the last swap (--rollback target)
└── .last_tag                    # short git sha of last deploy

# Docker-managed, survive restarts:
#   central-server-data (SQLite DB), caddy-data, caddy-config
```

### How upgrades work

The deploy script tars `central_server/` with excludes (`.env`, `__pycache__`, `*.db*`, virtualenvs), `scp`s the archive to the VPS, extracts it into `central_server.new`, and **atomically swaps**: `central_server` → `central_server.prev` → `central_server.new` → `central_server`. Then `docker compose up -d --build --remove-orphans` rebuilds the image and restarts the container. The named volume `central-server-data` is never touched, so the SQLite database and Alembic migration state persist.

`docker-compose.prod.yml` is re-synced from `deploy/prod/` each run, so the per-service `mem_limit` / `cpus` / `ulimits.nofile` caps (central-server `4g`/`3.0`, caddy `1g`/`1.0`, both `nofile` 65536 soft/hard) and the json-file log rotation (`max-size: 20m`, `max-file: 5` per service) apply automatically on the next deploy — no extra step. The 65536 file-descriptor ceiling is what lets the relay and Caddy hold WebSocket fan-out well past the dockerd default 1024 soft limit; the sizing rationale lives inline in the compose file and in [Capacity & revisit triggers](#capacity--revisit-triggers) below.

The deploy command validates the incoming `Caddyfile.<TlsMode>` in a throwaway `caddy:2-alpine` container **before** copying it over the live `Caddyfile` (a rejected config must never end up mounted — the running caddy would keep serving from memory, but the next container restart would take down all ingress), then runs `caddy reload` inside the caddy container after `compose up`: compose never restarts a running container for a bind-mounted file's content change, so without the reload a Caddyfile-only deploy would silently keep serving the old config. The reload is atomic (a parse failure keeps the old config live and fails the deploy loudly).

`stream_close_delay` on both proxied site blocks (apex/api and app.mobaide.com) does **not** spare established WebSockets from a reload. Caddy's documentation is explicit that it "delays streaming requests such as WebSockets from being forcibly closed when the config is unloaded" — it buys a 5-minute grace and then closes every stream registered against the unloaded config, all on one timer. So a reload that actually swaps config is a *deferred* mass disconnect, not a free one; a reload that changes nothing (`config is unchanged`) costs nothing.

That is why `--force` is used **only in `CloudflareOrigin` mode**. There it is unavoidable: Caddy diffs the *adapted JSON* and certificates are referenced by path, so swapping `tls/origin-*.pem` in place leaves the config byte-identical and a plain reload would keep serving the old certificate from memory while logging `config is unchanged` — see [TLS certificates](#tls-certificates). `LetsEncrypt` mode loads no certificate from a path at all (that Caddyfile has zero `tls` directives), so forcing there would buy nothing and would turn every no-op deploy into a disconnect of every agent tunnel and client socket.

### TLS certificates

Which host gets its certificate from where:

| Host | `LetsEncrypt` mode | `CloudflareOrigin` mode |
|---|---|---|
| `mobaide.com`, `api.mobaide.com` | Caddy, ACME HTTP-01 | Cloudflare Origin CA, loaded from `/opt/mobaide/tls/` |
| `www.mobaide.com` | Caddy, ACME HTTP-01 | Cloudflare Origin CA (must be an explicit SAN) |
| `app.mobaide.com` | Caddy, ACME HTTP-01 | Caddy, ACME HTTP-01 — **conditionally**, see below |

**The origin certificate must not be a wildcard.** `app.mobaide.com` is gray-cloud in both modes and has no `tls` directive in either Caddyfile, so Caddy is expected to ACME-issue for it. That only happens if no already-loaded certificate matches the host — and Caddy's certificate cache is global, not per-site. A Cloudflare Origin cert with SANs `*.mobaide.com, mobaide.com` therefore matches SNI `app.mobaide.com`, and Caddy disables automatic HTTPS for it, logging only:

```
"skipping automatic certificate management because one or more matching
 certificates are already loaded","domain":"app.mobaide.com"
```

The SPA then serves the Origin CA certificate, which no browser trusts, and `/data/caddy/certificates` stays empty. The documented override (`auto_https ignore_loaded_certs`) is **server-scoped**, so it would also force ACME for the orange-clouded apex and api — not a usable fix. Issue the origin cert for exactly `mobaide.com, www.mobaide.com, api.mobaide.com` instead.

`deploy_central.py --tls-mode CloudflareOrigin` inspects the installed cert during its preflight and warns (loudly, and again at the end of the run whatever its outcome) if its SANs shadow `app.mobaide.com` or if cert and key are not a pair. It does not block the deploy: a wildcard breaks the SPA, not the relay. When `openssl` cannot read the PEMs on the server it says the check **did not run** rather than reporting a clean result — an unreadable certificate must never be mistaken for a good one.

**Rotating the origin certificate.** Replacing `/opt/mobaide/tls/origin-*.pem` in place and running a plain `caddy reload` **does nothing** — Caddy diffs the *adapted JSON*, and certificates are referenced by path, so identical config means the reload logs `config is unchanged` and keeps the old certificate in memory. `deploy_central.py` therefore passes `--force` in this TLS mode. By hand:

```bash
# 1. verify the new pair BEFORE installing it — the two hashes must match.
#    A mismatch survives `caddy reload` (atomic; the old config stays live)
#    but bricks the next container restart, taking down the whole relay.
openssl x509 -in origin-cert.pem -noout -pubkey | openssl sha256
openssl pkey  -in origin-key.pem  -pubout | openssl sha256
openssl x509 -in origin-cert.pem -noout -ext subjectAltName   # no wildcard

# 2. install, then force the reload
cd /opt/mobaide && docker compose -f docker-compose.prod.yml exec -T caddy \
  caddy reload --force --config /etc/caddy/Caddyfile
```

Creating a new Origin Certificate in Cloudflare does not revoke the old one, so keeping the previous PEMs preserves rollback. `caddy validate` is a useful dry run: it prints the same `skipping automatic certificate management` list, so a host's ACME status can be confirmed before touching the live config.

**Diagnosing a TLS failure.** Both deploy scripts take `--diagnose-tls`, which probes without deploying and names which of {DNS, connect, local CA store, served certificate} is at fault — printing the served issuer and SANs when it is the server. It separates a broken *workstation* trust store from a broken *server* certificate with a control probe against a known-good public host, because both raise an identical `CERTIFICATE_VERIFY_FAILED`. If the workstation has no CA bundle at all (python.org macOS builds ship without one until `Install Certificates.command` runs), the scripts fall back to `certifi` automatically and say so.

```bash
python deploy/prod/deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin
python deploy/prod/deploy_web.py --diagnose-tls
```

### Deploying the web SPA (app.mobaide.com)

The browser client (`web/`) is served by **Caddy from disk** at `app.mobaide.com` — its own origin, same host. The site block reverse-proxies `/api`, `/ws`, `/relay` and `/proxy` to central-server (mirroring `web/vite.config.ts`'s dev proxy), so the SPA is fully same-origin with the relay and CORS never enters the picture. Everything else falls through to `try_files {path} /index.html` (client-side routing) with `Cache-Control: no-cache, must-revalidate` on `index.html` and unhashed public files, and `public, max-age=31536000, immutable` on Vite's content-hashed `/assets/*`. Because that fallback gives every path the same document, `web/index.html` declares `<link rel="canonical" href="https://app.mobaide.com/">`, which keeps Search Console from reporting `/login` and the other SPA routes as "Duplicate without user-selected canonical".

```bash
python deploy/prod/deploy_web.py                # npm run build + ship
python deploy/prod/deploy_web.py --skip-build   # reuse existing web/dist
python deploy/prod/deploy_web.py --rollback     # repoint at previous release
python deploy/prod/deploy_web.py --diagnose-tls # classify a TLS failure, deploy nothing
```

`web/node_modules` is not in the repo and the deploy never installs it, so run `npm ci` in `web/` on a fresh clone first — the preflight checks for it and says so rather than letting the build die inside vite as `tsc: command not found`.

Each deploy tars `web/dist`, extracts it to `/opt/mobaide/www/releases/<UTC-timestamp>-<sha>/`, and swaps the `current` symlink atomically (`ln -s` to a temp name, then `mv -T` — the symlink target stays **relative** so it resolves inside the caddy container's `/srv/www` bind mount). **No container restarts**; in-flight requests and live WebSockets are untouched. The newest 5 releases are kept, except that the release recorded in `www/.previous` is never pruned — repeated deploy/rollback cycles could otherwise push the rollback target out of the newest-5 window and silently degrade `--rollback` to the sorted-predecessor fallback (a newer, just-rejected build). `--rollback` repoints at the release that was live before the last swap (each deploy records it in `www/.previous`; a second `--rollback` toggles back, and hosts without the marker fall back to the sorted predecessor of `current`). There is no auto-rollback on a failed health probe — the swap is atomic, so a failure almost always means DNS/Caddy, not release content.

First-time prerequisites, in order:

1. Create the `app.mobaide.com` DNS record — **gray cloud in both TLS modes** (see [One-time setup](#one-time-setup)).
2. On a host bootstrapped **before** this feature landed, add `PAIRING_QR_TOKEN_TTL_SECONDS=600` to `/opt/mobaide/.env` by hand — bootstrap writes that line only into a freshly generated `.env`, and deploys never touch the file (operator-owned, never synced). It takes effect on the next `deploy_central.py` run (the container is recreated). Web pairing links assume the 600 s window; the 120 s code default is tuned for the scan-with-phone flow.
3. Run `deploy_central.py` **without `--skip-build`** so the extract step creates `www/releases` before compose mounts `./www` (a compose-created mount would be root-owned) and Caddy picks up the new site block. This run recreates both containers — the one-time quiet window; every live WebSocket drops once and clients auto-reconnect (session replay covers the gap). Afterwards, Caddyfile-only changes ship via the deploy command's `caddy reload` step; a reload that actually changes config gives established WebSockets the 5-minute `stream_close_delay` grace and then closes them (see [How upgrades work](#how-upgrades-work)), while a reload that changes nothing costs nothing.
4. Wait ~30 s (the server's ufw limits SSH to 6 connections per 30 s), then run `deploy_web.py`.

Hosts bootstrapped by the current `bootstrap-mobaide.sh` serve a placeholder page until the first web deploy; older hosts serve 404 on `/` of the app host between steps 2 and 3.

**Google Sign-In on the SPA origin.** The browser client reads its Google client id from `GET /api/version` (no build-time env var), so `GOOGLE_CLIENT_ID` in `/opt/mobaide/.env` is all the server needs — but `https://app.mobaide.com` must also be added to **Authorized JavaScript origins** on that OAuth client in Google Cloud Console, or the Sign in with Google button fails at runtime on the SPA while the Android and iOS flows keep working. The apex (`mobaide.com`) no longer needs to be listed at all — it used to carry the portal's own button; an entry left over from that deployment is harmless and can be removed. See ["Configuring Google Sign-In"](../README.md#configuring-google-sign-in).

**Sign in with Apple on the web.** The SPA reads the Apple **Services ID** from `GET /api/version`, so `APPLE_WEB_CLIENT_ID` in `/opt/mobaide/.env` is all the server needs — but the Services ID does not exist until it is created in the Apple developer portal, and one setting there decides whether this feature works or quietly corrupts accounts:

1. **Create a Services ID and set its primary App ID to `com.mobaide.app`** (the value of `APPLE_CLIENT_ID`). Apple derives the identity token's `sub` from the *group* a Services ID belongs to, so this is what makes a browser sign-in resolve to the account the iOS app already created. A standalone Services ID validates, signs in, and silently creates a **second account** for every existing Apple user — there is no server-side check that can detect it, because from the relay's side the `sub` is simply new.
2. Register the web domain and its Return URL on that Services ID: `app.mobaide.com` → `https://app.mobaide.com/login`. Popup mode never navigates to a Return URL — Apple only validates that the one sent matches a registered entry — but `AppleID.auth.init` refuses to run without it. A deployment set up before the portal was retired also carries `mobaide.com` → `https://mobaide.com/portal/login`; that Return URL is now unused and can be de-registered along with the `mobaide.com` domain entry.
3. Verify the domain by serving the `apple-developer-domain-association.txt` Apple issues at `/.well-known/`. For `app.mobaide.com`, drop the file into `web/public/.well-known/` and deploy: Vite copies `public/` into `dist/`, `try_files {path} /index.html` finds it, and `file_server` serves it (Caddy does not hide dotfiles). If you are keeping the legacy `mobaide.com` domain entry registered, the apex is reverse-proxied to central-server, which has no such route, so verifying it needs a `handle` block ahead of the proxy in the apex site block:

   ```
   handle /.well-known/apple-developer-domain-association.txt {
       root * /opt/mobaide/well-known
       file_server
   }
   ```
4. `APPLE_CLIENT_ID` stays set: the native audience is what the iOS app's own tokens carry, and `POST /api/auth/apple` accepts either identifier. Setting only the web one would break iOS sign-in.

Apple rejects `http://` and `localhost` Return URLs, so Apple sign-in cannot be exercised from `npm run dev`. That is the intended local behaviour — with `APPLE_WEB_CLIENT_ID` unset, `GET /api/version` reports null and neither button mounts.

**CSP hash for the theme-boot script.** `web/index.html` carries one inline pre-paint `<script>` (theme boot), pinned in the site block's `script-src` by its `sha256-…` hash — duplicated in **both** `deploy/prod/Caddyfile.*` files. Editing that script requires recomputing the hash (build, then `deploy_web.py` prints the expected value on mismatch), updating both Caddyfiles, and shipping them via `deploy_central.py` before the next web deploy. `deploy_web.py` hard-fails on drift; the failure mode if it ever slips through is cosmetic (theme flash on load).

### Capacity & revisit triggers

Sizing targets the **Tier B (stress)** load model from the 2026-08 web-client research: ~10,000 registered users, ~2,500 DAU, ~750 peak concurrent clients ≈ **~10,000 live WebSockets** (~6 per client + 1 per agent tunnel).

- **File descriptors:** ~10k sockets ≈ 15% of the 65,536 `nofile` ceiling — never the bottleneck at this tier.
- **Memory:** <300 MiB baseline + ~450 MiB of WS state with permessage-deflate **off** (`ws_per_message_deflate=False`, pinned in `central_server/__main__.py`; deflate-on would roughly double that to ~950 MiB, which is what made the old 1 GB cap the first bottleneck) + argon2 bursts (app-bounded at 2 workers). The 4 GB cap holds the tier with headroom.
- **CPU:** peak relay fan-out (~21k frame ops/s) is 40-70% of **one** core. The relay is a single asyncio event loop — it saturates one core long before the 3.0-cpu cap matters; the spare capacity covers argon2 threads. Vertical rescaling stops helping at that point.
- **`ws_max_queue`** (uvicorn, 32 frames per socket) stays at its default — the app-level send queues (10k/5k) are the real backpressure boundary, counted by the drop counters below.
- **`user_refresh_tokens` rows:** one INSERT per rotation, so ≈ DAU × 4/hour (a 15-minute access TTL with a 60 s leeway), plus one per browser restart now that a session survives closing the last tab. Revoked rows are reaped after a 24 h retention window, which lands Tier B around 40-90k rows / ~10 MB — trivial for SQLite, and *smaller* than before, since signing in again now revokes the row the presented cookie carried instead of leaving it live for its full 30 days. The axis that does scale is the 60 s sweeper: the table has only a `token_hash` primary key and a partial `(user_id) WHERE revoked_at IS NULL` index, so both `DELETE`s are full scans. Revisit with an index on `revoked_at` past roughly 500k rows.
- **`mobile_refresh_tokens` rows:** the same sweeper reaps this table with the same two `DELETE`s (expired-unrevoked at once, revoked after the 24 h window). It rotates now too: with the 60-minute `MOBILE_TOKEN_EXPIRE_MINUTES` default it gains about one row per hour per active phone × agent pair, where it used to gain one row per pairing. Its partial indexes are on `(agent_id, mobile_device_id)` and `(mobile_device_id)`, so those `DELETE`s are full scans as well and the same 500k-row revisit point applies.

**Observability:** the relay emits a `monitoring.sample` structured log line every 60 s (`MONITORING_INTERVAL_SECONDS`) with `loop_lag_ms_max` / `loop_lag_ms_avg` (event-loop lag over 1 s probe ticks), `rss_bytes` / `mem_limit_bytes` / `rss_pct_of_cap` (from cgroup v2 `memory.current` / `memory.max` — the numbers the OOM killer actually compares), `open_fds`, routing gauges (`agents_online`, `mobile_sockets`, `user_sockets`, `users_connected`, `attached_sessions`, `agent_queue_depth_max`/`_total`), and cumulative per-site send-queue drop counters (`queue_drops`, `queue_drops_total`). Grep it from `docker compose logs central-server`.

**Revisit triggers** — when any of these holds sustained, the next step is the **Redis relay split** (routing table, pairing registry, proxy futures, rate limiters → shared backend; a ~25-50k-registered-users project), **not** another rescale:

- event-loop CPU >70% sustained, or
- p95 `loop_lag_ms_max` >100 ms, or
- `rss_pct_of_cap` >70%, or
- a steadily climbing `queue_drops_total`.

### Public API surface

The relay never publishes its OpenAPI schema. `create_app` passes `docs_url=None, redoc_url=None, openapi_url=None`, so `/docs`, `/redoc` and `/openapi.json` are unregistered and return 404 on both `mobaide.com` and `api.mobaide.com`. The schema is a complete map of every route, parameter and model — including the admin dashboard and the release-publishing endpoint — and is treated as internal.

Set `ENABLE_API_DOCS=1` in `/opt/mobaide/.env` to get the docs back. They are then **admin-gated**: a request must carry either the `dashboard_token` cookie from `/dashboard/login` or an admin `Authorization: Bearer` token. Everything else — anonymous callers and non-admin users alike — still gets a 404, so the flag cannot re-expose the schema publicly. Browsers work as expected: sign in at `/dashboard/login` first, then open `/docs`.

Routers that serve HTML rather than an API (`/dashboard/*`, `/portal/*`, `/privacy.html`, and the marketing site's `/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`) and the release-pipeline endpoint `POST /api/internal/agent_manifest` are registered with `include_in_schema=False`, so even an enabled docs page describes only the real JSON surface.

The agent has the same knob with the same name and the same default. It matters more there than it looks: the agent binds `0.0.0.0` so DIRECT mode works over the LAN, so stock FastAPI docs would hand its whole session/project/git surface to anything on the same network. Leave it off outside local development.

### Security response headers

Four headers go on **every** response, from `central_server/central_server/security_headers.py` (a pure-ASGI middleware, registered last in `create_app` so it is the outermost of the three and stamps what the inner two produce):

| Header | Value |
| --- | --- |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Frame-Options` | `DENY` |
| `Cross-Origin-Opener-Policy` | `same-origin-allow-popups` |

`X-Frame-Options` duplicates every policy's `frame-ancestors 'none'` and exists only for browsers that predate CSP framing control. `/portal/cancel-deletion` overrides the referrer policy to `no-referrer` with a document `<meta>`, which wins over the header — that page carries a cancellation token in its URL.

`Content-Security-Policy` and `Permissions-Policy` are set on every response too — `/docs` and `/redoc` aside, which are CSP-exempt for the reason given below — but their value depends on the path. Both have their own section.

One gap, deliberate and worth knowing: an **unhandled** exception is turned into a 500 by Starlette's `ServerErrorMiddleware`, which wraps the entire user middleware stack, so that one response carries none of these. Everything else does, including every `HTTPException` (404 / 403 / 405) and every static file.

#### Cross-Origin-Opener-Policy

> **`Cross-Origin-Opener-Policy: same-origin` breaks Google Sign-In.** Do not "harden" this value. It severs the `window.opener` relationship the Google Identity Services popup posts the credential back through, and the symptom is a blank popup or a flow that stalls with nothing logged server-side.

Both plausible values isolate our window from a **cross-origin opener** — a page that opened us keeps no scriptable handle either way. They differ only in the other direction: `same-origin` additionally drops the handle on popups **we** open, and that handle is exactly what the sign-in popup uses.

The **SPA origin** requires `same-origin-allow-popups`, and every surface `central_server` renders takes the same value rather than the stricter one. That is a decision, not an oversight. No server-rendered surface opens a popup any more — the sign-in flows that did moved to the browser client — and `/dashboard/*` opens none either (no `window.open`, no `target="_blank"`, no Google button in `static/dashboard.js` or any dashboard template), so `same-origin` would protect against nothing that exists, while a popup added to either later would break under it silently. One value across every surface also keeps navigation between the same-origin `/`, `/portal/*` and `/dashboard/*` inside a single browsing context group.

The header is not verifiable in one browser. When FedCM is active Chrome renders the sign-in popup itself and COOP never comes into play; it matters on the non-FedCM path and in browsers without FedCM, which is why the check below asks for both.

`Cross-Origin-Embedder-Policy` and `Cross-Origin-Resource-Policy` are deliberately **not** set, and the issue that added COOP scoped both out. The reason is only strictly true of COEP: `require-corp` would block the `accounts.google.com/gsi/` frame and script, neither of which sends CORP. CORP is a different question — it governs who may embed *our* responses, not the Google frame — and has simply not been evaluated on its merits yet.

#### Permissions-Policy

Chosen by path, like the CSP:

| Surface | Policy |
| --- | --- |
| every surface `central_server` renders | `camera=(), microphone=(), geolocation=(), payment=(), usb=(), identity-credentials-get=()` |
| `app.mobaide.com` static SPA (served by **Caddy**) | the same list, but `microphone=(self)` and `identity-credentials-get=(self "https://accounts.google.com")` |

The SPA's `microphone=(self)` is voice input: the browser records while the user dictates and streams the audio to their own agent (docs/voice-input.md). Only that origin gets it; every central_server surface keeps `microphone=()`. Like any header in the Caddy site block, a change ships with `deploy_central.py` and needs a Caddy reload (`caddy reload --force`) before browsers see it.

> **`identity-credentials-get=(self)` on the SPA origin breaks Google Sign-In**, the same way `COOP: same-origin` does, and for the same reason it looks like the tighter choice. The Google button frame is created with `allow="identity-credentials-get"`, and a frame's `allow` only delegates a feature the embedding document's own policy grants to *that origin*. Naming the feature at all therefore obliges you to name Google's origin alongside `self`.

`identity-credentials-get` is spelled out rather than left off the list on purpose. Its default allowlist is `self`, so the empty allowlist in the baseline is what denies FedCM to our own same-origin documents as well — containment for an injected script on a surface that has no use for the API. `central_server` now serves that baseline everywhere: the FedCM allowance lives only in the Caddy site block, because the only Google button left is on the SPA.

Two things worth knowing before editing the feature list:

* **This header is Chromium-only.** Firefox and Safari ignore it entirely, so it hardens Chrome users and nobody else. It is not a substitute for any server-side control.
* **An unknown feature name is a console error on every page load** — `Error with Permissions-Policy header: Unrecognized feature: 'x'` — which is the same console the soak below asks you to read clean. Only names current Chrome still recognises belong here. `interest-cohort` (FLoC, dropped from Chrome in 2022) and its successor `browsing-topics` (removed in Chrome 150, July 2026) are both retired and deliberately absent.

#### Content-Security-Policy per surface

Headers are applied with `setdefault`, so a route that sets its own value still wins. The policy is chosen by path:

| Surface | Policy |
| --- | --- |
| `/` (the landing page) | `default-src 'self'; script-src 'self' https://www.googletagmanager.com https://www.googleadservices.com https://www.google.com https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net; img-src 'self' https://www.googletagmanager.com https://googleads.g.doubleclick.net https://www.google.com https://www.googleadservices.com https://pagead2.googlesyndication.com; connect-src 'self' https://pagead2.googlesyndication.com https://www.googleadservices.com https://googleads.g.doubleclick.net https://ad.doubleclick.net https://www.google.com; frame-src https://www.googletagmanager.com; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'` — the strict site policy below, widened with the origins [Google's CSP guide](https://developers.google.com/tag-platform/security/guides/csp) lists for Ads conversion tracking. The gtag.js loader is injected by `site.js` only after the visitor accepts the cookie banner; the served HTML itself is same-origin-only — see "The consent-gated Google Ads tag" in [`docs/website.md`](website.md) |
| `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, `/privacy.html` | `default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'` |
| `/portal/*`, `/dashboard/*` | `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` — one policy, **no** third-party origin and **no** `'unsafe-inline'`. The two surviving `/portal/*` pages load no script at all and would satisfy `_SITE_CSP` too; they take this one because `form-action 'self'` is what the cancel-deletion POST needs |
| everything else — `/api/*`, `/proxy/*`, `/relay/*`, `/static/*`, 404s | `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` |
| `/docs`, `/redoc` | **no CSP** — see below |
| `app.mobaide.com` static SPA (served by **Caddy**, not the app) | `default-src 'self'; script-src 'self' 'sha256-<theme-boot hash>' https://accounts.google.com/gsi/client https://appleid.cdn-apple.com/…/appleid.auth.js; style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style; img-src 'self'; connect-src 'self' https://accounts.google.com/gsi/; frame-src https://accounts.google.com/gsi/; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` |

The SPA row is enforced by the `(app_mobaide)` snippet in both `deploy/prod/Caddyfile.*` files, because static files never pass through `security_headers.py` — the Caddy site block therefore also duplicates the four universal headers plus `Permissions-Policy`. The SPA is now the **only** Google Sign-In popup surface (`web/src/auth/googleSignIn.ts` mounts GIS with `ux_mode: 'popup'` on `/login`, `/register` and `/account`), which is why it is the only place an `identity-credentials-get` allowance appears. Third-party origins appear in exactly two policies: this SPA row (sign-in) and the landing page's (the consent-gated Google Ads tag).

**Sign in with Apple costs exactly one `script-src` entry**, on the SPA. Under `usePopup` the SDK calls `window.open` — which no CSP directive governs — and reads the answer off a `postMessage` listener; the shipped bundle contains no `iframe`, no `fetch` and no `XMLHttpRequest`, and injects no style. So `appleid.apple.com` itself appears in no policy, and only `appleid.cdn-apple.com` does. It does mean COOP `same-origin-allow-popups` protects two flows rather than one: tightening it to `same-origin` severs the opener both providers reply through. Apple's SDK uses no FedCM, so it adds nothing to `Permissions-Policy`. The locale segment in the script path (`…/1/en_US/…`) is load-bearing — it must match `web/src/auth/appleSignIn.ts` and the pinned URL in both Caddyfiles. Proxied requests on the app host (`/api`, `/ws`, `/relay`, `/proxy`) still get the app's own path-routed policies from the table above. `connect-src 'self'` covers same-origin WebSockets (CSP3 treats `ws(s)://same-host` as `'self'`).

`script-src` grants no `'unsafe-inline'` and no `'unsafe-eval'` on any surface. That is why no template contains an inline `<script>`, an `onclick=` attribute or an `hx-on:` attribute any more — the dashboard's behaviour lives in `static/dashboard.js` and per-request values reach it through `data-` attributes, while the `/portal/*` pages carry no behaviour to place anywhere. Adding any of those three forms back does not degrade a page, it silently breaks it.

`'unsafe-inline'` appears in `style-src` only, and only on the one surface that hosts a Google Sign-In button: the `app.mobaide.com` SPA. The Google Identity Services client appends an inline `<style>` to the parent document the moment it loads, on top of the `accounts.google.com/gsi/style` sheet Google documents, and `renderButton({width})` emits an inline `style=` attribute. The SPA's policy is static — Caddy cannot see the server config — so it carries the Google origins unconditionally and the button simply never mounts when `GET /api/version` reports no client id. `img-src` stays `'self'`: the button and its Google logo render inside a cross-origin iframe, governed by Google's policy rather than ours. Since WEB-05 the SPA row has a **second, independent** reason to keep it: xterm.js injects a `<style>` element at runtime for the terminal screen, so tightening `style-src` on the app host would break the terminal even if the Google button ever left that origin. No other WEB-05 change touches the SPA policy — the terminal's WebSocket is same-origin (`connect-src 'self'`) and its assets are Vite-hashed local files.

`/docs` and `/redoc` are exempt on purpose. FastAPI renders both from a `cdn.jsdelivr.net` bundle with an inline bootstrap script, so any policy strict enough to be worth setting would break them, and one loose enough to fit is a list of third-party origins we do not control. Both stay unregistered unless `ENABLE_API_DOCS` is set, and admin-gated when it is. They still get the four universal headers and the baseline `Permissions-Policy`.

#### Rolling out a CSP change — the report-only soak

Deployment is manual and a bad CSP does not fail the `/api/health` check, so it can break a page while the deploy reports success. Before changing any policy:

1. Set `CSP_REPORT_ONLY=1` in `/opt/mobaide/.env` and deploy. The middleware emits `Content-Security-Policy-Report-Only` instead — violations are reported and nothing is blocked. **Four paths are the exception and stay enforced through a soak:** `/`, `/robots.txt`, `/sitemap.xml` and `/favicon.ico`. A soak is for a policy nobody has run yet; the three crawler files already served this exact policy enforced from `marketing/router.py`, and the landing page's widened policy is a strict superset of one that did — enforcing a widening can only unblock — so de-enforcing any of them buys nothing and costs a live protection. Everything else soaks, `/privacy.html` included — `legal/router.py` set no security header before this middleware, so that page's policy is exactly as new as the dashboard's.
2. Walk the surfaces in a browser with devtools open and confirm a clean console. Cover at minimum: `/portal/delete-account-info` and `/portal/cancel-deletion?token=x` (stylesheets, wordmark, webfont, and — on the second — the confirm form's POST, which is what `form-action 'self'` is there for), `/dashboard/users` (row expand, search swap, sort-header click, pager, delete-user modal), `/privacy.html`, `/`. The two `/portal/*` pages load no script, so a `script-src` mistake cannot show up there; the sign-in surfaces that would have caught one now live on the SPA origin, whose policy is in the Caddyfile and does not soak.

   **Report-only violations do not say "Refused to …"** — that is the enforcing wording. Chrome logs them as `Loading the script '…' violates the following Content Security Policy directive: "…"`, and — this is the part that catches people — at **info** level, so a console filtered to Errors shows nothing and the page looks clean. Firefox uses `(Report-Only policy) The page's settings would block …`. Each violation also fires a `securitypolicyviolation` event on `document`, with `disposition === "report"`, which is the reliable way to script the check.

   Chrome additionally emits a one-off *"Content-Security-Policy-Report-Only … will have no effect"* warning when the policy carries no `report-uri`/`report-to`. **Ignore it.** It is about report *delivery* to a collection endpoint, not about violation visibility — per-violation console logging and the DOM event both fire regardless, which is what makes this console-based soak work. Verified empirically against this build. Server-side collection is tracked in CLA-174.
3. Remove `CSP_REPORT_ONLY` and deploy again.

The default is **enforce** (`csp_report_only: bool = False`). A report-only default would mean the policy silently protects nobody if the flag is never flipped. There is no violation-collection endpoint — the soak is browser-console-based; server-side collection is tracked in CLA-174.

##### `CSP_REPORT_ONLY` does not cover COOP or Permissions-Policy

Both headers do have report-only counterparts — `Cross-Origin-Opener-Policy-Report-Only` and `Permissions-Policy-Report-Only` — but unlike CSP they report **only** through the Reporting API (`Reporting-Endpoints` plus a collector), never to the console. With no collection endpoint on this deployment (tracked in CLA-174) there is nothing to read, so `CSP_REPORT_ONLY` leaves both alone and their only rehearsal is a staged deploy plus a manual sign-in. Once CLA-174 lands, `Cross-Origin-Opener-Policy-Report-Only: same-origin` served alongside the enforcing `same-origin-allow-popups` is the way to answer the question below without a two-browser sweep. After changing either value, sign in end to end on `https://app.mobaide.com/login` and run the `/account` delete-account panel's Google re-auth, twice — once on a stock Chrome (FedCM active) and once on a Chrome launched with `--disable-features=FedCm`. A COOP mistake is invisible on the FedCM path, because there Chrome renders the popup itself, so a single-browser check proves nothing.

Repeat both on `app.mobaide.com` when the `(app_mobaide)` Caddy block changes — it carries its own copy of these headers and is the other surface with a Google button.

#### HSTS

`Strict-Transport-Security` is emitted by **Caddy**, not the app: `central-server` is reached over plain HTTP on the compose network, and browsers ignore HSTS on a non-HTTPS response. Both `deploy/prod/Caddyfile.LetsEncrypt` and `deploy/prod/Caddyfile.CloudflareOrigin` carry `header Strict-Transport-Security "max-age=300"` in each site block.

**`max-age=300` is the first rung of a ratchet, not the target.** HSTS is effectively irreversible — once a browser caches a `max-age`, lowering it only reaches clients that come back before the old value expires, and there is no way to reach the rest. Advance one rung per deploy, verifying between each (CLA-172):

1. `max-age=31536000`, still no `includeSubDomains`. Soak a week.
2. Audit every subdomain for HTTPS-only serving. `api.mobaide.com`, `www.mobaide.com` and `app.mobaide.com` are this same Caddy; `get.mobaide.com` is GitHub Pages and needs "Enforce HTTPS" confirmed there.
3. Add `includeSubDomains` — it binds every current *and future* `*.mobaide.com` name, including any short-lived staging host. Soak a month.
4. Consider `preload` only after that, and only accepting that removal takes months.

On `CloudflareOrigin`, leave Cloudflare's own HSTS setting **off**: two sources for one header is how you lose track of what is actually pinned.

#### CORS — deliberately absent

`CORSMiddleware` is not installed, and its absence is a decision rather than an oversight. The Android and iOS clients use OkHttp and `URLSession`, which are not browsers and do not preflight. The browser client exists now — and is served **same-origin** on `app.mobaide.com` (the Caddy site block proxies `/api`, `/ws`, `/relay`, `/proxy` to central-server) precisely so no CORS policy is ever needed. Keep it that way: a cross-origin API host would reopen this decision for zero benefit.

Its absence is now also load-bearing rather than merely convenient. The two cookie-authenticated API routes (below) require a custom `X-Mobaide-Session` header, which works as a CSRF control *because* a custom header makes the request non-simple: the browser preflights, the `OPTIONS` gets a bare 405 with no CORS middleware to answer it, and the real request is never sent. Installing `CORSMiddleware` — especially with `allow_credentials=True` — would dismantle that.

#### Browser session cookies

The SPA's refreshable session keeps its **refresh token in an HttpOnly cookie** so a session survives closing the last tab; the 15-minute access token stays in the tab's `sessionStorage` and still travels as an `Authorization` bearer. All cookie mechanics live in `central_server/auth/session_cookie.py`.

Two cookies are always written — and always deleted — in the same response: `__Host-mobaide_refresh` (HttpOnly, the credential) and `__Host-mobaide_signed_in` (readable, a single bit that lets a booting tab skip a pointless restore request). Both are `Secure`, `SameSite=Strict`, `Path=/`, no `Domain`. Pairing them in one response is what keeps the marker from outliving or under-living the credential.

`USER_SESSION_COOKIE_SECURE` (default `true`) controls `Secure` **and the name prefix together**, because RFC 6265bis makes `__Host-` conditional on `Secure` — a browser silently drops the whole cookie if they disagree. At `false` the same cookies are set unprefixed, and the server logs a `session_cookie.insecure` warning at startup. Set it false only for local development over plain http, and only for Safari: Chrome and Firefox already accept `Secure` cookies on `http://localhost`.

The **credential** is read under exactly one name, chosen by that flag. Accepting the unprefixed name as a fallback would cancel the only thing the prefix buys: `__Host-` forbids a `Domain` attribute, which is what stops a sibling host writing a cookie this origin will send. Without that, `get.mobaide.com` could set `mobaide_refresh=<its own token>; Domain=mobaide.com` and fixate an attacker-chosen session on a victim. The **marker** is deliberately read under both names — it is one bit, not a credential, and the client cannot know at build time which name the server chose — but only the prefixed form is trusted enough to trigger a destructive purge.

`SameSite=Strict` is not sufficient on its own here, which is why the header gate exists. `SameSite` is scoped to the registrable domain, not the origin, and `get.mobaide.com` is served from GitHub Pages (see docs/release-publishing.md) — third-party infrastructure that is *same-site* with `app.mobaide.com`. A page there could otherwise POST to `/api/auth/logout` with the cookie attached. Only `POST /api/auth/refresh` and `POST /api/auth/logout` ever read a cookie for authorization; every other `/api/*` route authenticates on the `Authorization` header alone (`auth/dependencies.py::get_api_user`) and is CSRF-immune by construction.

Mobile is unaffected: native clients never send `session_transport: "cookie"`, so they keep receiving the refresh token in the response body.

One more cookie lives on the registrable domain by design: `mobaide_ads`, issued by `POST /api/ads/consent` when the visitor accepts the marketing site's banner (`Domain=mobaide.com`, JS-readable, deliberately not `__Host-`) so the SPA can read the landing page's Google Ads click id at sign-up. Because a domain cookie can be planted by any sibling host, the value is HMAC-signed with a key derived from `JWT_SECRET_KEY`, the endpoint accepts same-origin requests only (`Sec-Fetch-Site` / `Origin` are browser-controlled), and the arrival timestamp inside it is capped at the server clock. A signature alone still proves only that *this server* minted the value — an attacker's own HTTP client can spoof those headers and have sibling-host script plant the result — so the payload also carries a keyed fingerprint of the network the Accept came from (exact IPv4, /64 for IPv6), and sign-up honours the *consent* (the licence for the hashed-email fallback) only when it arrives from that same network. A click id holds no personal data and is kept whichever network the sign-up comes from, so a changed address costs at most the fallback. It is not a credential and **no server route reads the cookie itself**: its fields reach the server only inside the optional `ad_attribution` field of the register / Google / Apple request bodies, where the signature is verified before anything is stored — an unsigned or tampered value is ignored, never a 422 — see [Google Ads conversion upload](#google-ads-conversion-upload-agent-paired). Decline posts the value back to `POST /api/ads/consent/withdraw`, which clears the attribution from every account created with that cookie (the account row keeps the cookie's signature for exactly this lookup) and expires the cookie. **`CloudflareOrigin` mode needs the `trusted_proxies` block at the top of its Caddyfile**: the apex is behind Cloudflare while `app.mobaide.com` is not, and without it Caddy replaces Cloudflare's forwarded client address with the edge address, so a fingerprint minted on the apex could never match a sign-up — see [Client IP behind Caddy](#client-ip-behind-caddy).

**Deploy `deploy_central.py` before `deploy_web.py`, and roll back in the reverse order.** The two ship independently, and `RefreshableSessionRequest` does not set `extra="forbid"`, so a pre-cookie server silently *ignores* `session_transport` and returns the refresh token in the body — which a cookie-era SPA discards. The symptom is not a loud failure: browser sessions simply end every `USER_SESSION_ACCESS_EXPIRE_MINUTES` with no diagnostic. The window is the gap between the two deploys, so the ordering is the whole mitigation. A body-token fallback would reintroduce the JS-readable 30-day credential this design exists to remove.

#### WebSocket Origin allow-list

Every WS endpoint checks the `Origin` header **before** accepting the handshake (`central_server/ws_origin.py`): absent `Origin` is allowed (native apps and the agent send none — the header only means something from a browser), loopback origins are always allowed (Vite dev/preview need zero config), and anything else must match `WS_ALLOWED_ORIGINS` (comma-separated, default `https://app.mobaide.com`). Rejected handshakes close pre-accept, which uvicorn surfaces as HTTP 403, and log `ws_origin.rejected`. Relay auth rides `?token=` bearers rather than cookies, so this is anti-CSWSH defense in depth, not the primary control — and it must stay that way. A session cookie now exists on the SPA origin and a WS handshake carries it like any other same-site request, so a WS endpoint that authenticated off `websocket.cookies` would promote this gate to the primary control, which it is not built for: it structurally allows any loopback origin on any port.

### Client IP behind Caddy

`docker-compose.prod.yml` sets `FORWARDED_ALLOW_IPS=*` on `central-server`. Caddy runs as a separate container, so its bridge IP — not `127.0.0.1` — is the peer uvicorn sees; at uvicorn's default the `X-Forwarded-For` header is ignored and every request is attributed to Caddy. That collapses the per-IP password-reset quota onto one shared bucket (locking out real users while doing nothing to an attacker) and fills the access log with `172.x` addresses. The wildcard is safe here only because `central-server` is `expose`-only and never published, so Caddy on the compose network is the only possible peer. Do not copy it to the dev `docker-compose.yml`, which publishes `9000:9000` directly.

In `CloudflareOrigin` mode there is a second hop: the proxied hosts (`mobaide.com`, `www`, `api`) reach Caddy from a Cloudflare edge, and Caddy trusts no incoming `X-Forwarded-For` by default — it replaces it with the peer address, so uvicorn would attribute every apex request to Cloudflare. `Caddyfile.CloudflareOrigin` therefore opens with a global `servers { trusted_proxies static … }` block listing Cloudflare's published ranges (`https://www.cloudflare.com/ips-v4` and `/ips-v6`; pinned by `tests/test_caddy_trusted_proxies.py`, refresh both when Cloudflare changes them). With the edge trusted, Caddy forwards `X-Forwarded-For: <visitor>, <edge>` and uvicorn reads the leftmost entry. `app.mobaide.com` is gray-cloud in both modes and unaffected; `Caddyfile.LetsEncrypt` has no proxy in front and trusts nothing.

### Google Ads conversion upload ("Agent paired")

The rollout as a whole — the two-lane design, stage history, the Google Ads / Cloud / Firebase account configuration and the pause / change / remove runbook — is [docs/agent-paired-conversion.md](agent-paired-conversion.md); this section is the relay-side mechanics.

The relay can report **one offline click conversion per account** to Google Ads. The first time an account pairs an agent — either pairing direction, both finish in `_complete_pairing` — a compare-and-set on `users.first_paired_at` fires exactly once and hands the user id to `central_server/ads/` for a fire-and-forget upload through the **Data Manager API** (`POST https://datamanager.googleapis.com/v1/events:ingest`). The identifier is the `gclid` / `wbraid` / `gbraid` the landing page captured at sign-up (see *The consent-gated Google Ads tag* in [docs/website.md](website.md)) when the click is at most 90 days old, otherwise the SHA-256 of the normalised account email (enhanced conversions for leads) — never both. The upload carries the consent state the banner actually collected (`adUserData: CONSENT_GRANTED`, `adPersonalization: CONSENT_DENIED`), uses the email fallback only for accounts whose sign-up request carried the consent marker, and clears the account's attribution columns afterwards whatever the outcome — except when Google refused the *credentials* rather than the event (see the log vocabulary below), in which case the attribution stays pending for the next process. An upload interrupted by a restart is re-scheduled at the next startup (`ads.resume_pending`). Every event carries a `transactionId` — a SHA-256 digest of the identifier and the event time, i.e. of fields the report carries anyway, so it discloses nothing further — which is what makes a re-send after a lost response or a restart a duplicate Google drops rather than a second conversion. A purge job — hourly, and once at startup before anything is resumed — drops attribution older than 90 days, measured from the click or from sign-up, whichever is earlier: the click stamp comes from the visitor's clock, so the server-stamped sign-up time caps retention.

**The in-app lane.** This upload is the *website* lane: it can only ever see accounts whose sign-up carried the landing page's click id or consent. Accounts created inside the mobile apps are measured by the apps themselves through Google Analytics for Firebase, which fires off the same compare-and-set — `_complete_pairing` also broadcasts the `account.first_agent_paired` user event and exposes the stamp on `GET /api/auth/me`. That lane, its consent defaults, the untracked Firebase config files and the `GOOGLE_SERVICES_JSON_BASE64` release secret are documented in [docs/app-conversion-measurement.md](app-conversion-measurement.md).

**Why not the Google Ads API.** Its `uploadClickConversions` method has been closed since 2026-06-15 to developer tokens without prior conversion-upload history (`CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE`), and Google names the Data Manager API as the replacement. That API needs no developer token and no API-version knob and takes the same click ids and hashed emails, but it is *fast-fail* (a bad row rejects the whole request; there is no partial-failure envelope) and *asynchronous*: a 200 carries only a `requestId`, and whether the click matched is published through `requestStatus:retrieve` between roughly 30 minutes and 24 hours later.

**Fully inert unless configured.** Startup logs exactly one of `ads.disabled reason=not_configured`, `ads.misconfigured missing=<names>` (some variables set, some not — stays disabled) or `ads.enabled api=data_manager customer_id=…`. A leftover `GOOGLE_ADS_DEVELOPER_TOKEN` or `GOOGLE_ADS_API_VERSION` from the Google Ads API era is ignored, with one `ads.obsolete_setting` warning.

| Variable | Required | Value |
|---|---|---|
| `GOOGLE_ADS_CUSTOMER_ID` | yes | The Ads account that **owns the conversion action** — the Data Manager API's operating account must be the conversion customer itself, not a parent. Dashes are stripped; anything but digits counts as unset. |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | when access is via a manager | The manager (MCC) account id, sent as the login account. Leave unset when the OAuth user is a direct member of the Ads account. |
| `GOOGLE_ADS_OAUTH_CLIENT_ID`, `GOOGLE_ADS_OAUTH_CLIENT_SECRET` | yes | A **Desktop app** OAuth client from the Google Cloud Console, on a project with the **Data Manager API** enabled. |
| `GOOGLE_ADS_OAUTH_REFRESH_TOKEN` | yes | Minted with `deploy/prod/google_ads_oauth.py` (below) for the `https://www.googleapis.com/auth/datamanager` scope. |
| `GOOGLE_ADS_CONVERSION_ACTION_ID` | yes | Numeric id of the `Agent paired` action (Goals → Conversions → Summary → the action; the number after `ctId=` in the URL). It must be an *Import → track conversions from clicks* action. |
| `GOOGLE_ADS_CLICK_WINDOW_DAYS` | no | The action's click-through window (default 90, the value the rollout plan configures; Google's own default is 30). A click older than this is a guaranteed no-match, so the uploader tries the hashed-email fallback instead. Clamped to 1..90 — the 90-day retention the policy promises. |

**Setup runbook**

1. Google Cloud Console: enable the *Data Manager API*, set the OAuth consent screen to **In production** — a consent screen left in *Testing* issues refresh tokens that expire after 7 days, and the symptom is `ads.credentials_rejected reason=oauth_refresh_failed` about a week after deploy (uploads are deferred, not lost: re-mint the token and recreate the container) — and create an OAuth client of type **Desktop app**. The `datamanager` scope is classed *sensitive*, so an unverified app shows Google's "hasn't been verified" interstitial once, to the operator minting the token; brand or scope verification is not needed for that.
2. Google Ads: the action must be *Import → Other data sources → Track conversions from clicks*, counting **One** per click. The hashed-email fallback additionally needs, under Goals → Conversions → Settings, the Customer Data Terms accepted **and the separate "Enhanced conversions for leads" row turned on** — the unified "Enhanced conversions" switch above it does *not* flip that row (verified on this account on 2026-09-03), and the row's method dropdown (Google tag / Google Tag Manager) is a diagnostics label, not a gate. With the row off, Google rejects every hashed-email upload up front with a bare `400 INVALID_ARGUMENT` whose hidden field violation is `DESTINATION_ACCOUNT_NOT_ENABLED_ENHANCED_CONVERSIONS_FOR_LEADS`, and the account's attribution is cleared — turn the uploader off before touching that page. Set `GOOGLE_ADS_CLICK_WINDOW_DAYS` to the action's click-through window if it is not 90.
3. Workstation: `python deploy/prod/google_ads_oauth.py --client-id <id> --verify --customer-id <ads account> --conversion-action-id <ctId> [--login-customer-id <manager>]`. It opens the browser (sign in with the Google account that administers the Ads account), prints the three `GOOGLE_ADS_OAUTH_*=` lines to stdout, and with `--verify` sends a **validate-only** upload to the real destination — Google checks the token, the enabled API, the account path and the action id without recording anything. `PERMISSION_DENIED` there means the signed-in account does not reach the Ads account: add the manager id, or sign in as a user of the account. On a macOS python.org build that has never run *Install Certificates.command* the script verifies against certifi (with a warning) instead of dying on `CERTIFICATE_VERIFY_FAILED`.
4. Paste the values into `/opt/mobaide/.env` and run `deploy_central.py` (`--skip-build` is enough when the source is already deployed: compose recreates the container on an `env_file` change).

**Verifying and reading the logs**

```bash
docker compose logs central-server | grep -E 'ads\.|pairing\.(first_agent_paired|conversion_)'
```

`pairing.conversion_uploaded user_id=… identifier=<gclid|gbraid|wbraid|hashed_email> request_id=…` means Google **accepted** the request — not yet that the click matched. A best-effort follow-up polls Google's verdict 45 minutes after the upload and every two hours while it is still `PROCESSING`, for up to a day, and logs `pairing.conversion_status user_id=… request_id=… status=<SUCCESS|FAILED|PARTIAL_SUCCESS> records=… errors=<REASON:count,…> warnings=…` **once**, when the verdict is terminal (at the deadline it logs whatever status it last saw); a poll that still sees `PROCESSING` writes nothing, so an empty grep in the first hours is expected. A restart drops the follow-up, and the action's *Diagnostics* tab in Google Ads stays the source of truth, with attributed conversions showing there within about a day. `pairing.conversion_upload_failed … reason=<r> status=<s> message=<STATUS: google message>` is terminal and clears the account's attribution. The `reason` vocabulary: `http_4xx` (permanent — `INVALID_ARGUMENT` a malformed row, `NOT_FOUND` a wrong customer or action id), `retries_exhausted` (three attempts over network errors / 429 / 5xx, 1 s then 4 s apart), `bad_response` (a 200 whose body is not JSON or carries no `requestId`, or an unexpected status). Refused **credentials** are the one non-terminal failure: a rejected refresh token (`oauth_refresh_failed`), a 401 (`http_401`) or a 403 (`http_403` — `PERMISSION_DENIED` usually means a token minted for the wrong scope, a signed-in account that does not reach the Ads account or a missing manager id, or a Data Manager API that is not enabled) logs one `ads.credentials_rejected reason=… status=… message=…` warning, after which this process sends nothing more and every affected upload logs `pairing.conversion_upload_deferred user_id=… identifier=… reason=…` with its attribution kept; fix the setting, recreate the container, and `ads.resume_pending` picks the backlog up (the 90-day purge still bounds it). Rejections that only happen during processing — an expired click (`EVENT_TOO_OLD`) — never fail the upload; they surface in the status line's `errors=` and in Diagnostics, and a hash that simply matches no click is not an error at all. A missing account-side flag (enhanced conversions for leads, the data terms) is different: it fails the upload up front as `http_4xx` and clears the attribution. `pairing.conversion_skipped … reason=no_consent` is the normal outcome for every account that did not sign up through the consented web path. Click ids, emails and hashes never appear in the logs; the `request_id` is what Google support asks for. The helper's `--verify` is the smoke test; the first real pairing after deploy is the end-to-end test.
### Rollback

- **Automatic**: only if `docker compose up` itself fails (and only on a full deploy, not `--skip-build`) — the script swaps `central_server.prev` back in and rebuilds. Nothing was serving the new source at that point, so there is no evidence to lose.
- **A failed `/api/health` check never rolls back.** Compose already came up, so the containers running were built from the deployed source: an automatic swap-and-rebuild would destroy the state the logs describe, and it fires just as readily for failures no source rollback can fix (DNS, Caddy, the Cloudflare edge, or this workstation's own trust store). The script prints the logs command and the rollback command instead.
- **Manual**: `deploy_central.py --rollback` swaps `central_server.prev` back in on demand — the deliberate response to a failed health check, and to a regression that only surfaces later.

### Verify

```powershell
curl.exe https://mobaide.com/api/health          # 200 + {"status":"ok",...}
curl.exe -I https://mobaide.com                  # cert issuer visible
curl.exe -I https://app.mobaide.com              # SPA (or placeholder) + CSP/HSTS headers
curl.exe https://app.mobaide.com/api/health      # 200 — proxied prefix reaches central-server
ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml ps'
ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml logs --tail=60 central-server'
```

Then point the Android app (Settings → Add connection) at `https://mobaide.com`, log in with your seeded admin credentials, and pair a local agent from your workstation.

---

## Per-machine agent deployment

The agent (`mobaide_agent/`) runs natively on the user's workstation as a long-lived process that dials OUT to `central-server` over WebSocket. No container, no inbound tunnel, no public hostname, and no port-forwarding are required on the workstation — the relay architecture handles NAT traversal for you.

The Android app never talks to the agent directly; it always goes through `central-server`.

### Prerequisites

- Python 3.12+ (the production `install.ps1` flow on Windows auto-installs Python 3.12 via `winget`, with a python.org-installer fallback when `winget` is unavailable; the conda dev path still needs it pre-installed)
- `git`, `curl`
- Claude CLI installed on the same machine (the agent spawns `claude` via PTY — see https://claude.ai/code). **v2.1.108 or newer is recommended** — the agent presets `ENABLE_PROMPT_CACHING_1H=1` on every spawn to enable the 1-hour prompt-cache TTL introduced in that release ([release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)); older CLIs will simply ignore the variable. The CLI must be on the agent's `PATH` — Windows installs auto-detect `claude.cmd` from the installing user's `%APPDATA%\npm` and pin it as `MOBAIDE_CLAUDE_BIN` in `<prefix>\mobaide_agent\.env`; the Linux/macOS unit files ship with `%h/.npm-global/bin` and `%h/.local/bin` already appended. To use a non-standard install location set `MOBAIDE_CLAUDE_BIN=/absolute/path/to/claude` in `<prefix>/agent.env`.
- Codex CLI (optional — the second managed agent; install with `npm install -g @openai/codex`). When present, Windows installs auto-detect `codex` from the installing user's `%APPDATA%\npm` and pin its absolute path as `MOBAIDE_CODEX_BIN` in `<prefix>\mobaide_agent\.env`, exactly mirroring the `MOBAIDE_CLAUDE_BIN` auto-detection above; otherwise the agent probes the same npm-global locations on `PATH`. To use a non-standard install location set `MOBAIDE_CODEX_BIN=/absolute/path/to/codex` in `<prefix>/agent.env`.
- Antigravity CLI (optional — the third managed agent; binary `agy`, a standalone install — **not** an npm package). When present, Windows installs auto-detect `agy` from the installing user's `%USERPROFILE%\.local\bin` or the `Antigravity` program folders and pin its absolute path as `MOBAIDE_AGY_BIN` in `<prefix>\mobaide_agent\.env`, exactly mirroring the `MOBAIDE_CODEX_BIN` auto-detection above; otherwise the agent probes `~/.local/bin` and the standard install locations on `PATH`. To use a non-standard install location set `MOBAIDE_AGY_BIN=/absolute/path/to/agy` in `<prefix>/agent.env`. (Antigravity auth is host-side — pre-authenticate `agy` on the host via Google Sign-In; there is no API-key env var and no in-app login.)
- A paired agent: `RELAY_AGENT_ID` + `RELAY_AGENT_TOKEN` issued by `central-server`. Pair from the Android app: **Settings → Agents → Pair new**, or via the admin REST API.

### Windows (conda — recommended for development)

```powershell
conda create -n mobaide python=3.12 -y
conda activate mobaide
pip install -e .\mobaide_agent

# Generate a prepared .env (random JWT_SECRET_KEY, RELAY_SERVER_URL prefilled).
# Use a custom prefix for dev so it doesn't touch your install at C:\Program Files.
python -m mobaide.agent.config init-env --prefix .\mobaide_dev_prefix

# Run from the prefix (so the .env at <prefix>\mobaide_agent\.env is loaded).
$env:MOBAIDE_AGENT_PREFIX = (Resolve-Path .\mobaide_dev_prefix).Path
python -m mobaide
```

Render the pairing QR for the mobile app to scan (CLA-50):

```powershell
mobaide-agent pair-qr --wait --prefix .\mobaide_dev_prefix
```

For the full local stack (Caddy + central-server in Docker + agent native), use `start-all.ps1` from the repo root.

### Windows (Scheduled Task) — automated

**End-user / production install:** run the one-liner in a regular (non-admin) PowerShell window:

```powershell
irm https://get.mobaide.com/install.ps1 | iex
```

The script self-elevates via UAC, auto-installs Python 3.12 (`winget` fast path, python.org installer fallback), stages the signed release into `<prefix>\releases\<version>\venv\` (default prefix `C:\Program Files\MobaideAgent`), writes `<prefix>\current_version`, and registers a **per-user Scheduled Task** named `MobaideAgent`. The task is pinned to the installing user (`$env:USERNAME`) with `-LogonType Interactive` and triggered `-AtLogOn`, so the agent process tree runs in that user's session — `%USERPROFILE%` resolves to `C:\Users\<u>`, and the Claude CLI in the agent's PTY sees the user's `~/.claude` credentials and skills, just like a desktop terminal session does. After the task starts, the script waits for the agent to report healthy and for the supervisor to write the pairing nonce file, then renders the pairing QR code in the console window for the operator to scan from the Mobaide mobile app. This ordering ensures the QR is shown only once the agent is ready to accept a pairing claim, eliminating the race where scans during boot left `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` empty.

The installer also writes `<prefix>\bin\mobaide-agent.cmd` and appends `<prefix>\bin` to the installing user's PATH (`HKCU\Environment`, the same account the task and the `.env` ACL belong to). `mobaide-agent show-qr`, `mobaide-agent re-pair` and the other maintenance verbs then work from any **new** terminal window; the elevated install window can use them right away. The shim reads `<prefix>\current_version` on every call, so auto-updates and rollbacks never break it. Verbs that change files under `%ProgramFiles%` (such as `re-pair`) need an elevated terminal, and the command says so when it hits that. Installs made before the shim existed get it by re-running the one-liner (re-runs keep the pairing).

> **Requires an interactive logon.** The task only runs while the installing user has an interactive session (the lock screen is fine; a full logoff stops the task). Headless / unattended Windows hosts (build agents, CI runners, Windows servers without a logged-in user) are out of scope for this installer — there is no service-account mode. Multi-user machines: each user re-runs the installer in their own session and gets their own task pinned to themselves (no shared agent state across local accounts), mirroring Linux's `$SUDO_USER` semantics.

#### Architecture

The agent runs under a per-user Scheduled Task. The Scheduled Task action points at a **version-independent supervisor** at `<prefix>\svc\mobaide_launcher.py` so the registered task `Execute` / `-Argument` path stays valid across upgrades and rollbacks.

```
<prefix>\
├── current_version              # text file: "X.Y.Z"
├── bin\mobaide-agent.cmd        # version-independent CLI shim (on the user PATH)
├── releases\<version>\
│   ├── .installed               # completion sentinel — release is runnable
│   └── venv\                    # per-release venv (mobaide + deps)
├── trust\central_server.pub     # pinned manifest-signing public key
└── svc\
    └── mobaide_launcher.py      # stdlib-only Scheduled-Task supervisor
```

The registered task action is:

```
Execute  : <pythonw.exe resolved by Resolve-Pythonw at install time>
Argument : -u "<prefix>\svc\mobaide_launcher.py" --prefix "<prefix>"
```

`mobaide_launcher.py` is fetched from `https://get.mobaide.com/windows_launcher.py` during install. It is co-versioned with `install.ps1` itself — the `publish-gh-pages` workflow ships both files in the same push — so installer changes that depend on a new supervisor API can never desync. For self-hosted distributions, override the URL via `MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`.

On every task start, the supervisor (`mobaide_launcher.py`) is launched by `pythonw.exe` — the GUI-subsystem variant of CPython, which does not allocate a console window. This is what prevents the transient py.exe window from appearing at logon or during `irm install.ps1 | iex`. Because `pythonw.exe` binds its stdio to no-op handles, the supervisor redirects its own `sys.stderr` to `<prefix>\logs\launcher.err.log` at startup so diagnostics (config errors, Python tracebacks) survive. It then joins a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so everything it starts dies with it: `Stop-ScheduledTask` / `schtasks /End` terminate only the supervisor, and before the job an orphaned agent kept the port and the relay credentials it booted with, so the next task start exited on the port clash (Task Scheduler last result 3) and `re-pair` never completed. The supervisor then reads `<prefix>\current_version`, resolves the active release's `python.exe`, and spawns `python.exe -m mobaide.launcher` as a subprocess. The launcher does crash-loop detection + rollback exactly as on Linux/macOS — it rewrites `<prefix>\current_version` to a stable **complete** sibling release (one carrying the `.installed` sentinel, or the legacy `mobaide-agent.exe` console script) if the active one crashes 3 times within 60 s; an incomplete release swapped into place but not yet finalized is never selected or booted. On Windows, `mobaide.launcher` uses `subprocess.Popen` + a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` instead of `os.execv` (which creates a new PID on Windows, breaking the supervisor's `poll()` supervision — Python issue #9148). Every `subprocess.Popen` / `subprocess.run` call in the Windows agent code path also passes `creationflags=CREATE_NO_WINDOW` as defense-in-depth so child processes inherit the no-window behaviour. The agent's stderr is captured to `<prefix>\logs\agent.err.log` and its stdout (structlog records, uvicorn access logs, every `pairing_monitor.*` and `relay_client.*` event) to `<prefix>\logs\agent.out.log` (each rotated at 5 MB, one backup kept).

> **CPython 3.13.0 caveat:** on CPython 3.13.0, `pythonw.exe` inside a venv still allocates a transient console due to [CPython issue #126084](https://github.com/python/cpython/issues/126084). This was fixed in 3.13.1. The installer's `winget` fast path targets Python 3.12, which is unaffected; if you are using a self-managed 3.13.0 environment and observe a brief window flash, upgrade to 3.13.1+.

When the launcher subprocess exits with code 75 (`_EXIT_RESTART_REQUESTED` — emitted on successful pair or staged update), the supervisor re-resolves `current_version` and respawns immediately. This in-process loop gives the user a sub-second post-pair restart; Task Scheduler's outer `-RestartCount` / `-RestartInterval` (1-minute floor) catches the supervisor-level failure case.

Task Scheduler restart-on-failure settings: `-RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)`. `-ExecutionTimeLimit (New-TimeSpan -Seconds 0)` disables the default 72-hour task TTL. `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries` keep the agent running on laptops regardless of power state.

> **Note (breaking change vs. older builds):** prior builds registered the agent as a LocalSystem SCM service. That model gave the agent process tree `%USERPROFILE%=C:\Windows\system32\config\systemprofile`, which hid the user's `~/.claude` credentials and skills from the Claude CLI in the agent's PTY. Re-running `install.ps1` on a host with the SCM-service design `sc.exe delete`s the legacy `MobaideAgent` service, removes the legacy `<prefix>\svc\venv\` (the pywin32 host venv from that era), and registers the new Scheduled Task. An even-older `cmd.exe /c launcher.cmd` registration is also cleaned up by the same path.

#### Task management

```powershell
Get-ScheduledTask     -TaskName MobaideAgent           # state (Ready / Running / …)
Get-ScheduledTaskInfo -TaskName MobaideAgent           # LastRunTime, LastTaskResult, NextRunTime
Start-ScheduledTask   -TaskName MobaideAgent
Stop-ScheduledTask    -TaskName MobaideAgent
Unregister-ScheduledTask -TaskName MobaideAgent -Confirm:$false   # uninstall the task

# Task-Scheduler start/stop/failure events:
#   Event Viewer -> Applications and Services Logs ->
#     Microsoft -> Windows -> TaskScheduler -> Operational

# Supervisor stderr (pythonw.exe startup errors, config errors before the agent forks).
Get-Content "$Prefix\logs\launcher.err.log" -Tail 50
# Agent stderr (Python tracebacks, port conflicts).
Get-Content "$Prefix\logs\agent.err.log" -Tail 50
# Agent stdout (structlog: pairing flow, relay client, HTTP, auto-scan).
Get-Content "$Prefix\logs\agent.out.log" -Tail 100
```

A supervisor installed before the kill-on-close job leaves the launcher and agent running after `Stop-ScheduledTask`. Re-run the installer to replace it; it stops every process running from the prefix first. The `mobaide-agent` verbs that restart the agent (`re-pair`, `pair`, `pair-qr --wait`, `fetch-trust-key`) also stop such leftovers before they start the task again. They skip the restart when run from inside one of the agent's own terminal sessions, which the stop would end first.

The task runs as the installing user (one task per user); no Group Managed Service Account / virtual account configuration is needed or supported. To pair a second user on the same machine, that user re-runs `install.ps1` in their own session — they get their own Scheduled Task pinned to themselves.

### Linux (systemd) and macOS (launchd) — automated

**End-user / production install (recommended):** pull a signed release from `central_server` via the install script:

```bash
# Linux
curl -fsSL https://get.mobaide.com/install.sh | sudo bash

# macOS — runs as the current user; no sudo required
curl -fsSL https://get.mobaide.com/install.sh | bash
```

The script detects the OS, verifies the Ed25519 manifest signature, stages the
release under `<prefix>/releases/<version>/venv/` (`/opt/mobaide` on
Linux, `/usr/local/mobaide` on macOS), writes the `releases/<version>/.installed`
completion sentinel as its last install step, writes `<prefix>/current_version`,
and registers the supervisor. (The sentinel is the same "complete release"
contract the auto-updater uses — see
[versioning-and-updates.md](versioning-and-updates.md#install-layout) — so a
release is only ever selected once it is fully installed and runnable.)

* **Linux** — installs `mobaide-agent.service` (systemd). `ExecStart` is the
  generated `<prefix>/bin/mobaide-agent-launcher.sh` wrapper (the same wrapper
  macOS launchd already uses), not the older inline
  `bash -c '... python -m mobaide.launcher'`. The wrapper self-heals a
  missing, corrupt, or incomplete current-release venv by rolling
  `current_version` forward to the newest sibling release that is complete
  (`.installed` sentinel, or the legacy console-script fallback) before exec;
  the in-venv launcher then does the 3-strike crash-loop rollback. The unit is
  configured with `StartLimitBurst=10 / StartLimitIntervalSec=600` so those
  recovery paths have room to retry a stable sibling
  release before systemd stops the unit, and `RestartForceExitStatus=75` so
  the updater's restart trigger is honoured. The agent runs as the user
  who invoked `sudo` (resolved from `$SUDO_USER`) so it can read/write
  the developer's `/home/<user>/<repo>` source trees. Override with
  `MOBAIDE_RUN_AS_USER=<user>` for CI / non-interactive provisioning.
* **macOS** — installs `~/Library/LaunchAgents/com.mobaide.agent.plist`
  (launchd). `ThrottleInterval=10` gives the boot journal the same rollback
  window on macOS. The agent runs as the logged-in GUI user (not root):
  the installer resolves the target user (`MOBAIDE_RUN_AS_USER` →
  `$SUDO_USER` → console owner), `chown`s the install prefix to that user,
  and loads the LaunchAgent into that user's GUI domain via
  `launchctl bootstrap gui/<uid>` (run with `launchctl asuser` from root),
  falling back to a legacy `launchctl load` on headless/no-GUI hosts.
  Override the user with `MOBAIDE_RUN_AS_USER=<user>`.

The install script auto-generates `<prefix>/mobaide_agent/.env` with a
random `JWT_SECRET_KEY` and prefilled `RELAY_SERVER_URL=wss://mobaide.com`,
so the agent boots in **pairing-pending** state immediately. The install
script starts the supervisor, waits for the agent to be healthy and for the
service to write the pairing nonce file, then renders the pairing QR code
for the operator to scan from the Mobaide mobile app. If the operator misses
the QR, run `mobaide-agent show-qr` on the agent host to re-render the most-recent
pending QR (or initiate a fresh one if the cached nonce expired). See
[docs/qr-pairing.md](qr-pairing.md) for the full flow.

**The `mobaide-agent` command.** The release venv's console script moves with
every update, so the installer writes a small shim at
`/usr/local/bin/mobaide-agent`, which is on the default PATH of mainstream
Linux distros and of macOS (`/etc/paths`). It reads `<prefix>/current_version`
on every call, so auto-updates and rollbacks never break it. Run it as the
agent's user. Run as root (`sudo mobaide-agent …`), it switches to that user
first: root never executes the user-writable release, and the CLI never leaves
root-owned files the agent cannot read. A `re-pair` started that way may not
be able to restart the service itself; it then prints the
`sudo systemctl restart mobaide-agent` to run. On RHEL-family distros sudo's
`secure_path` leaves out `/usr/local/bin`; there, write
`sudo /usr/local/bin/mobaide-agent …`. When
`/usr/local/bin/mobaide-agent` already exists and was not written by the
installer, or the install ran without root, the shim goes to
`<prefix>/bin/mobaide-agent` instead and the installer prints that full path.
Installs made before the shim existed get it by re-running the install
one-liner (re-runs keep the pairing); until then, run
`<prefix>/releases/$(cat <prefix>/current_version)/venv/bin/mobaide-agent`.

Until paired, the agent exposes `pairing_pending: true` on `/api/health`.
Re-running the installer never clobbers paired credentials; pass
`--force` to `python -m mobaide.agent.config init-env` to regenerate
(which still preserves `RELAY_AGENT_ID`/`RELAY_AGENT_TOKEN`).

**From-source / developer install:** use `deploy/setup.sh` (sets up a flat venv from the local repo checkout). The script renders the systemd unit inline against the flat-venv layout (`/opt/mobaide/venv/...`), so this path does NOT require the versioned `releases/` layout that the end-user script uses.

```bash
sudo bash deploy/setup.sh
# or, to pin which user owns the agent on a multi-user host:
MOBAIDE_RUN_AS_USER=alice sudo -E bash deploy/setup.sh
```

### macOS file-access permissions (Full Disk Access / TCC)

On macOS, the first time the agent — or a `claude` session it spawns — reads files in
a privacy-protected folder (Documents, Desktop, Downloads, iCloud Drive, or a
removable/network volume), macOS posts a **Transparency-Consent-Control (TCC)
permission dialog** (e.g. *"python3.x would like to access files in your Documents
folder"*) on the **host's desktop**. The agent runs as a per-user LaunchAgent, so that
dialog targets the logged-in Mac user — a remote mobile user never sees it, and the
process that touched the file blocks until it is answered, so the session looks frozen.

The agent does **not** try to suppress these prompts. macOS only grants Full Disk
Access to `.app` bundles, not to bare interpreters like the agent's venv `python`, and
there is no supported way to pre-authorize file access on an unmanaged Mac without an
MDM-pushed PPPC profile. Instead the agent makes the wait **visible**: a per-session
startup watchdog that sees essentially no output flags the likely block and raises an
`awaiting_os_permission` attention, which the mobile app surfaces as **"Action needed
on your Mac"** (notification + badge) rather than a blank terminal. `/api/health` also
reports `macos_full_disk_access` (`true`/`false` on macOS, `null` elsewhere) for support.

**To resolve:** approve the dialog on the Mac. macOS remembers each grant per folder
category, so it is a one-time approval per category, not a recurring prompt. To
eliminate the prompts entirely (power users), wrap the launchd-launched binary in a
code-signed `.app` bundle and grant *that* Full Disk Access — Apple's only supported
path for a background tool. It is intentionally **not** automated here, because
child-process TCC inheritance to the `claude` subprocess tree is not guaranteed across
macOS versions.

### Voice input (speech-to-text)

Dictation is transcribed **on the agent's PC**, never on a Mobaide server. It is **off by default**: the agent downloads and starts nothing until the user enables voice from an app (or the CLI below), and agent updates install code only — they never fetch the model. The wire contract (routes, states, error codes, `capabilities.voice`, `voice.changed`) is in [PROTOCOL.md](../PROTOCOL.md).

**What enabling costs.** The model is NVIDIA Parakeet TDT 0.6B v3 (int8 ONNX, 25 European languages including Russian and Ukrainian; no Japanese, Chinese, Korean, Arabic, Hindi or Turkish), run on the CPU by sherpa-onnx — the GPU is never used.

| | |
|---|---|
| Download | ~682–690 MB (670 MB model + 12–19 MB of runtime wheels, depending on the platform) |
| Disk | up to ~730 MB |
| Memory | none while idle; about 700 MB while a dictation runs |
| Warning thresholds | apps warn (`low_resources`) below 11.5 GiB RAM, 4 logical CPUs or 2 GiB free disk; the user may still enable |
| Unsupported | Python other than 64-bit CPython 3.12–3.14 (a 32-bit or free-threaded build), CPUs other than x86-64/ARM64, Linux with musl or glibc < 2.17, or too little free disk |

**How it runs.** A helper process (`python -P -m mobaide.voice.worker`, same interpreter as the agent) starts on the first dictation, at below-normal priority with 2 inference threads and no console window, and exits after 3 idle minutes, which returns all of its memory to the OS; it is also recycled after an hour or 500 requests. The agent process itself never loads the model. The helper reads stdin from the agent and exits when the agent goes away, so it is never left behind. While a dictation is open or a transcript cleanup is running, the auto-updater defers its restart (a forced update, and the `mobaide-agent update` CLI, do not wait).

**Where files live.** `<state prefix>/voice/` — the install prefix (`/usr/local/mobaide`, `/opt/mobaide`, `C:\Program Files\MobaideAgent`) when writable, else the per-user data dir — outside `releases/`, so release pruning and rollback never touch it; the systemd unit's `ReadWritePaths` already covers it. `settings.json` holds the on/off choice and the last install outcome, `models/<model-id>/` the model plus its `NOTICE` (CC BY 4.0 attribution: created by NVIDIA, int8 ONNX export by k2-fsa/sherpa-onnx), and `runtime/<sherpa-onnx-version>-<python tag>-<platform>/` the sherpa-onnx runtime, unpacked from hash-pinned PyPI wheels without pip — it is not a dependency of the agent wheel, so a platform without a wheel can never break an agent install. A Python upgrade fetches a new runtime; the model stays. The opt-in transcript cleanup (docs/voice-input.md) keeps one small prompt file in a sibling directory, `<state prefix>/voice-cleanup/`; it needs no download and does not depend on the model. `MOBAIDE_VOICE_DIR` overrides the location (a relative path is anchored under the state prefix; the CLI reads it from the agent's environment or `.env`, and `--voice-dir` overrides both). Removing the agent with **Also uninstall Mobaide from this computer** deletes the voice files too (see *Uninstalling the agent*).

**Downloads.** Model files come from the `voice-model-parakeet-tdt-0.6b-v3-int8` release in the public releases repo (`github.com`), falling back to the pinned upstream Hugging Face revision (`huggingface.co`); the two runtime wheels come from PyPI (`files.pythonhosted.org`) — allow those hosts on a filtering proxy. Every file's size and SHA-256 are pinned in the agent's catalog (`mobaide/voice/catalog.py`), so a captive-portal page or a truncated mirror is rejected (`hash_mismatch`) and the next source is tried. Downloads honour `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY`, use certifi, retry with backoff and resume from `*.part` files with HTTP Range — after a network drop, a sleeping laptop, or an agent restart (which resumes the install by itself). A `failed` install waits for the user to retry. Offline, enabling fails with `network`; once installed, dictation needs no internet.

**CLI** (run as the agent's user — elevated on Windows — so the files stay writable for the agent):

```bash
mobaide-agent voice status [--json]     # state, cost, host warnings, self-test speed
mobaide-agent voice install             # foreground download + verify + self-test
mobaide-agent voice remove [--yes]      # turn off and delete the files
mobaide-agent voice benchmark           # dictation latency on this PC (needs voice installed)
```

The CLI works on the same files and lock as the running agent; the apps see the result on their next status read.

**Privacy.** Audio is held in memory only, transcribed, and dropped; neither audio nor transcript text is logged or written to disk — logs carry only sizes, durations, timings and error codes. The one way the text leaves the PC is the opt-in transcript cleanup (web, Claude Code sessions): the agent hands the transcript to the user's own Claude Code CLI, which sends it to Anthropic under that login; the agent still never logs or stores it (docs/voice-input.md, *Transcript cleanup*).

**Testing without the model.** `MOBAIDE_VOICE_FAKE=1` swaps in a tiny generated model and a deterministic engine (transcripts read `fake transcript <n> ms`) under `<state prefix>/voice-fake/`; the install state machine and helper process are real. `tests/e2e/manual_stack.py up --fake-voice` sets it.

**Troubleshooting.** `hash_mismatch` behind a proxy usually means TLS interception or a captive portal; `disk_full` needs the space shown in `voice status`; `selftest_failed` means the runtime or model could not load (the message names the cause, e.g. a missing glibc symbol) — `voice remove` then `voice install` reinstalls both.

### Threat model

The agent now runs with the developer's full UID privileges and has read/write access to `$HOME`. A compromise of the relay channel — i.e. a stolen `RELAY_AGENT_TOKEN` or a compromised central server — gains the same access the developer has interactively:

- their source trees, SSH keys, GPG keys, browser cookies, `~/.aws/credentials`, `~/.netrc`, `~/.gitconfig` (which may reference signing keys / tokens);
- any process the user can spawn (the relay can request arbitrary `command` in `create_session`, and since proto-v7 it also injects `resolved_command` for custom agent types — same trust class);
- any host the user can reach over SSH.

Treat `RELAY_AGENT_TOKEN` as equivalent to the developer's interactive shell credential. Rotate it immediately on any suspected central-server compromise; revoke it via the admin API before decommissioning a host. Rotate `JWT_SECRET_KEY` (in `<prefix>/agent.env`) on the same trigger — it signs the agent's local API tokens and is equivalent in trust to a stolen `RELAY_AGENT_TOKEN`. The systemd unit retains `NoNewPrivileges=yes`, `ProtectSystem=strict` with explicit `ReadWritePaths=$HOME /opt/mobaide`, and `PrivateTmp=yes` for residual hardening, but these protect against privilege escalation, not against a relay-token compromise.

This is the same trust model adopted by comparable per-user developer tools (code-server, JupyterLab, VS Code Server). It is incompatible with multi-tenant hosting on a single agent install — pair one agent per developer.

#### Project paths outside `$HOME`

`ReadWritePaths` only grants writes to the agent user's `$HOME` and the install prefix. A project pointing at e.g. `/srv/code/myrepo` or `/data/repos/foo` will fail with `OSError: [Errno 30] Read-only file system` at worktree creation, because `ProtectSystem=strict` mounts the rest of the OS hierarchy read-only. To support such projects, drop in an override that extends the writable list — keeping the rest of the hardening intact:

```bash
sudo systemctl edit mobaide-agent.service
# In the editor, paste (replace /srv/code with your real path):
[Service]
ReadWritePaths=/srv/code
# Save and exit; systemd writes the override to
# /etc/systemd/system/mobaide-agent.service.d/override.conf
sudo systemctl restart mobaide-agent
```

Multiple paths can be listed whitespace-separated. Re-running the install scripts will not overwrite the `*.d/override.conf` files.

### Migrating from the legacy `mobaide` system user

Pre-`/home`-fix installs created a dedicated `mobaide` system account and ran the agent as that account with `ProtectHome=yes` — which silently broke any project pointing at `/home/<user>/...` (PermissionError on the `.git` probe). Re-running either install script on an existing host:

* re-renders the systemd unit with `User=$SUDO_USER` (or `MOBAIDE_RUN_AS_USER`),
* `chown -R`s `/opt/mobaide` to the new user,
* `userdel`s the legacy `mobaide` account (best-effort; skipped if still in use).

No data migration is needed — `projects.json`, releases, and `agent.env` survive the chown.

### Linux (systemd, manual)

The service unit at `deploy/mobaide-agent.service` is a **template** with `__RUN_USER__`, `__RUN_GROUP__`, `__RUN_HOME__` placeholders that the install scripts substitute. If you wire it up by hand, fill those in yourself; `mobaide-agent.service` is otherwise configured for the versioned `releases/` layout (`/opt/mobaide/releases/<version>/venv/` with a `current_version` pointer). Its `ExecStart` is `<prefix>/bin/mobaide-agent-launcher.sh` (the self-healing launcher wrapper the install scripts generate via `write_launcher_script`), so a by-hand wireup must also create that wrapper — the install scripts are the source of truth for its contents.

```bash
VERSION="0.1.0"      # replace with the actual agent version being installed
RUN_USER="$(id -un)" # the user whose code the agent will manage

sudo mkdir -p /opt/mobaide/releases/${VERSION}
sudo cp -r mobaide_agent/. /opt/mobaide/releases/${VERSION}/

sudo python3 -m venv /opt/mobaide/releases/${VERSION}/venv
sudo /opt/mobaide/releases/${VERSION}/venv/bin/pip install /opt/mobaide/releases/${VERSION}

echo "${VERSION}" | sudo tee /opt/mobaide/current_version

python -m mobaide.agent.config init-env   # generates <prefix>/mobaide_agent/.env with random JWT_SECRET_KEY and prefilled RELAY_SERVER_URL

sudo sed \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    -e "s|__RUN_GROUP__|$(id -gn ${RUN_USER})|g" \
    -e "s|__RUN_HOME__|$(getent passwd ${RUN_USER} | cut -d: -f6)|g" \
    deploy/mobaide-agent.service \
    | sudo tee /etc/systemd/system/mobaide-agent.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now mobaide-agent

sudo chown -R "${RUN_USER}:$(id -gn ${RUN_USER})" /opt/mobaide
```

### Service management

```bash
systemctl status mobaide-agent
journalctl -u mobaide-agent -f
sudo systemctl restart mobaide-agent
```

### Uninstalling the agent

**Remotely, from a client.** When you remove an agent in the Android, iOS or
web app, the **Also uninstall Mobaide from this computer** option is checked
by default. iOS shows it as a **Delete & Uninstall** button. The option
appears only when the agent's build reported that it can uninstall itself;
see the self-uninstall entry in `PROTOCOL.md`. Here is what happens:

1. The relay sends the agent an `agent_uninstall` order. An online agent gets
   it at once. An offline agent gets it the next time it connects with its old
   credentials, within 30 days.
2. The agent blanks `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` in
   `<prefix>/mobaide_agent/.env`, so it can never reconnect, even if the
   rest fails.
3. The agent removes itself. It deletes only the known install entries
   (`releases/`, `trust/`, `mobaide_agent/`, `run/`, `state/`, `logs/`,
   `svc/`, `bin/`, `current_version`, logs, `users.json`, `projects.json`,
   `voice/`, …) and then the prefix itself if that left it empty. It never
   runs a blanket `rm -rf` of a prefix that `MOBAIDE_AGENT_PREFIX` may have
   pointed somewhere shared.
   First it stops voice input and deletes the voice model and runtime itself,
   on every OS, so they go even when an older `install.sh` wrote the Linux
   helper below. A voice dir moved by `MOBAIDE_VOICE_DIR` (or reached through
   a symlinked `voice/`) loses only what `mobaide-agent voice remove` deletes
   plus `settings.json` and `.lock`, and is then removed if it is left empty.
   - **Linux:** the agent runs unprivileged, so it only drops
     `<prefix>/run/uninstall-requested`. `install.sh` installs a root-owned
     `mobaide-agent-uninstall.path` unit that watches for that marker. It
     starts `mobaide-agent-uninstall.service`, which runs
     `/usr/local/libexec/mobaide-agent-uninstall`. That script disables and
     stops `mobaide-agent`, deletes the install and the
     `/usr/local/bin/mobaide-agent` command, removes all three units and
     finally itself.
     Installs made before this helper existed get a partial uninstall:
     everything is deleted except an `exit 0` stub at
     `bin/mobaide-agent-launcher.sh`, so the leftover unit stays quiet.
     Finish by hand with
     `sudo systemctl disable mobaide-agent && sudo rm -f /etc/systemd/system/mobaide-agent.service && sudo rm -rf /opt/mobaide`,
     or re-run the installer first so a later remote uninstall is complete.
   - **macOS:** the agent deletes the install,
     `~/Library/LaunchAgents/com.mobaide.agent.plist` and the
     `/usr/local/bin/mobaide-agent` command, then runs
     `launchctl bootout gui/<uid>/com.mobaide.agent`, which stops it.
     `/usr/local/mobaide` itself stays behind (empty) when `/usr/local`
     is root-owned, and so does the command when `/usr/local/bin` is; run,
     it says the agent is gone and to `sudo rm` it.
   - **Windows:** the agent removes `<prefix>\bin` from the user's PATH and
     deletes the `MobaideAgent` Scheduled Task. It
     starts a one-shot `MobaideAgentCleanup` task, run by the base Python
     interpreter outside the install, that retries deleting
     `%ProgramFiles%\MobaideAgent` until the agent's files unlock, then
     removes itself. Python, which the installer may have installed, is
     left alone.
4. The agent exits 0.

An offline agent removed this way keeps its order for 30 days, and
`install.sh` / `install.ps1` re-runs keep the existing `.env` credentials.
So reinstalling on that host within the window uninstalls the fresh install
the moment it connects, which is seconds after the installer restarts the
service. To reuse the machine, delete `<prefix>/mobaide_agent/.env` **before**
reinstalling. The installer then writes a fresh one and starts a new pairing,
and nothing matches the old order.

A developer or source run, i.e. any interpreter outside
`<prefix>/releases/`, never deletes or rewrites anything. It only stops
talking to the relay.

**By hand.** Remove the pieces the installer created:

```bash
# Linux
sudo systemctl disable --now mobaide-agent mobaide-agent-uninstall.path
sudo rm -f /etc/systemd/system/mobaide-agent.service \
  /etc/systemd/system/mobaide-agent-uninstall.{path,service} \
  /usr/local/libexec/mobaide-agent-uninstall /usr/local/bin/mobaide-agent
sudo systemctl daemon-reload && sudo rm -rf /opt/mobaide
# macOS
launchctl bootout gui/$(id -u)/com.mobaide.agent
rm -f ~/Library/LaunchAgents/com.mobaide.agent.plist
sudo rm -rf /usr/local/mobaide /usr/local/bin/mobaide-agent
```

```powershell
# Windows (elevated)
Unregister-ScheduledTask -TaskName MobaideAgent -Confirm:$false
Remove-Item -Recurse -Force "$env:ProgramFiles\MobaideAgent"
```

On Windows, also remove `C:\Program Files\MobaideAgent\bin` from your user
`Path` (Start → "Edit environment variables for your account").

Then remove the agent from your account in the app.

### Android client configuration

1. Open the Mobaide app
2. Go to **Settings → Add connection**
3. Set the **Server URL** to your central-server base URL, e.g. `https://mobaide.com`
4. Sign in (or sign up), then go to **Agents → Scan QR**
5. Scan the QR code rendered on the agent host (printed by the install
   script after the service is up and healthy, or via `mobaide-agent show-qr`). The
   server pushes the credentials directly to the agent over a
   pairing WebSocket; no token ever appears in the mobile UI. See
   [docs/qr-pairing.md](qr-pairing.md) for the protocol details.
6. The agent will show up as online in the app within a few seconds

### Troubleshooting

#### Agent won't start

```bash
journalctl -u mobaide-agent -n 100
```

Common causes:

- Missing `JWT_SECRET_KEY` in `.env`
- Port 8000 already in use (change `PORT`, or stop the other process)
- Python version < 3.12
- Claude CLI not on the service's `PATH` — the agent returns HTTP 503 with `claude_cli_missing: true` and a `searched_paths` list (each entry tagged with its source: `override:`, `env:`, `PATH:`, `fallback:`). The Windows installer auto-detects `claude.cmd` in the installing user's `%APPDATA%\npm` and writes its absolute path as `MOBAIDE_CLAUDE_BIN` into the agent's `.env`, so a clean install on a host where the user already ran `npm install -g @anthropic-ai/claude-code` works without manual edits. Manual fix paths:
  - Linux/macOS: install the CLI under `~/.npm-global/bin`, `~/.local/bin`, `/usr/local/bin`, or `/opt/homebrew/bin` (all in the resolver's POSIX fallback list); or add `MOBAIDE_CLAUDE_BIN=/absolute/path/to/claude` to `<prefix>/agent.env`.
  - Windows: edit `<prefix>\mobaide_agent\.env`, set `MOBAIDE_CLAUDE_BIN=C:\Users\<you>\AppData\Roaming\npm\claude.cmd`, then restart the task: `Stop-ScheduledTask MobaideAgent; Start-ScheduledTask MobaideAgent`. Or, if you installed Claude CLI after the agent: re-run `irm https://get.mobaide.com/install.ps1 | iex` (the install script will surgically backfill `MOBAIDE_CLAUDE_BIN` without touching pairing credentials), or run the `set-env` verb directly:
    ```powershell
    & "$env:ProgramFiles\MobaideAgent\releases\<version>\venv\Scripts\python.exe" `
      -m mobaide.agent.config set-env --key MOBAIDE_CLAUDE_BIN `
      --value "C:\Users\<you>\AppData\Roaming\npm\claude.cmd"
    Stop-ScheduledTask -TaskName MobaideAgent
    Start-ScheduledTask -TaskName MobaideAgent
    ```
- Codex CLI not on the service's `PATH` (only relevant if you use the optional Codex agent) — the Windows installer auto-detects `codex` in the installing user's `%APPDATA%\npm` and writes its absolute path as `MOBAIDE_CODEX_BIN` into the agent's `.env`, mirroring the Claude detection above, so a clean install on a host where the user already ran `npm install -g @openai/codex` resolves Codex without manual edits. If `codex` was installed after the agent, or lives outside the npm-global / PATH locations: re-run `irm https://get.mobaide.com/install.ps1 | iex` (the install script surgically backfills `MOBAIDE_CODEX_BIN` without touching pairing credentials), or run the `set-env` verb directly:
    ```powershell
    & "$env:ProgramFiles\MobaideAgent\releases\<version>\venv\Scripts\python.exe" `
      -m mobaide.agent.config set-env --key MOBAIDE_CODEX_BIN `
      --value "C:\Users\<you>\AppData\Roaming\npm\codex.cmd"
    Stop-ScheduledTask -TaskName MobaideAgent
    Start-ScheduledTask -TaskName MobaideAgent
    ```
- Antigravity CLI not on the service's `PATH` (only relevant if you use the optional Antigravity agent) — the agent returns HTTP 503 with an `antigravity_cli_missing` marker and a `searched_paths` list. There is no pre-spawn auth probe: `agy` has no sign-in check, so an unauthenticated `agy` simply shows its Google Sign-In prompt in the PTY — pre-authenticate `agy` on the host before launching a session (there is no in-app login route). The Windows installer auto-detects `agy` in the installing user's `%USERPROFILE%\.local\bin` or the `Antigravity` program folders and writes its absolute path as `MOBAIDE_AGY_BIN` into the agent's `.env`, mirroring the Claude and Codex detection above, so a clean install on a host where the user already installed `agy` resolves Antigravity without manual edits. If `agy` was installed after the agent, or lives outside those / `PATH` locations: re-run `irm https://get.mobaide.com/install.ps1 | iex` (the install script surgically backfills `MOBAIDE_AGY_BIN` without touching pairing credentials), edit `<prefix>\mobaide_agent\.env` directly, or run the `set-env` verb:
    ```powershell
    & "$env:ProgramFiles\MobaideAgent\releases\<version>\venv\Scripts\python.exe" `
      -m mobaide.agent.config set-env --key MOBAIDE_AGY_BIN `
      --value "C:\Users\<you>\.local\bin\agy.exe"
    Stop-ScheduledTask -TaskName MobaideAgent
    Start-ScheduledTask -TaskName MobaideAgent
    ```

#### Agent not showing as online in the Android app

Check the agent's logs for `relay.connection_failed` entries. Common causes:

- `RELAY_SERVER_URL` scheme wrong (`wss://` for HTTPS central-server, `ws://` for local plain HTTP). A cleartext `ws://` URL is refused at boot by default — set `MOBAIDE_AGENT_ALLOW_INSECURE_WS=1` (or run with `MOBAIDE_ENV=test`) to allow a plaintext relay for loopback/local testing; `wss://` is unaffected.
- `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` don't match the bcrypt hash stored in central-server's DB for this agent — re-pair via the app or re-issue the token via the admin API
- Central-server unreachable — verify with `curl https://mobaide.com/api/health`
- For a local dev stack: the workstation's OS trust store must include Caddy's root CA (see `start-all.ps1`, which auto-installs it)
- **macOS `CERTIFICATE_VERIFY_FAILED` / `unable to get local issuer certificate`:** a python.org framework Python ships an empty OpenSSL CA store until its bundled *Install Certificates.command* runs. The relay now verifies `wss://` against the bundled `certifi` store regardless of the host interpreter; on macOS the launchd launcher (`<prefix>/bin/mobaide-agent-launcher.sh`) additionally exports `SSL_CERT_FILE` from the venv's `certifi` bundle so the auto-update/pairing HTTPS paths verify too. Behind a TLS-intercepting proxy, pin your corporate root CA by adding `SSL_CERT_FILE` to the launchd plist's `EnvironmentVariables` (the launcher leaves a pre-set value untouched), then reload it from your own login session: `launchctl bootout gui/$(id -u)/com.mobaide.agent 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mobaide.agent.plist`.
- A flapping link logs `relay.reconnecting` with a growing `delay` and `uptime`: the reconnect backoff now only resets to base after a connection survives `RELAY_RECONNECT_STABILITY_THRESHOLD` seconds (default `60.0`) and adds decorrelated jitter, so a connection that keeps dropping young keeps its escalated, jittered delay. Raise the threshold on a host with a known-unstable uplink to suppress reconnect-log churn.
- **Cloudflare Bot Fight Mode (HTTP 403 error 1010):** if the relay is behind Cloudflare, agent v0.1.3+ sends `User-Agent: mobaide-agent/<version>` on all outbound calls (manifest polling, trust-key fetch, pairing initiate), which passes Cloudflare's default Bot Fight Mode. Operators running Super Bot Fight Mode with JA3/JA4 fingerprinting set to "Block" must add a WAF Skip rule: `(http.user_agent wildcard "mobaide-agent/*") and (http.request.uri.path starts_with "/api/")`. Older agents should be upgraded to v0.1.3+.

#### Health check failing locally

```bash
curl http://localhost:8000/api/health
# new install layout:
cat /opt/mobaide/agent.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
# legacy / from-source layout fallback:
cat /opt/mobaide/mobaide_agent/.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
```
