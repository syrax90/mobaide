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
3. On your workstation: add an `~/.ssh/config` entry so `ssh mobaide` works with key-based auth.

### Deploy

```bash
cd <repo-root>
python deploy/prod/deploy_central.py                              # LetsEncrypt (Cloudflare gray cloud)
python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin  # CF Origin Cert (orange cloud)
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

The deploy command validates the incoming `Caddyfile.<TlsMode>` in a throwaway `caddy:2-alpine` container **before** copying it over the live `Caddyfile` (a rejected config must never end up mounted — the running caddy would keep serving from memory, but the next container restart would take down all ingress), then runs `caddy reload` inside the caddy container after `compose up`: compose never restarts a running container for a bind-mounted file's content change, so without the reload a Caddyfile-only deploy would silently keep serving the old config. The reload is atomic (a parse failure keeps the old config live and fails the deploy loudly) and, thanks to `stream_close_delay` in the app site block, established WebSockets survive it.

### Deploying the web SPA (app.mobaide.com)

The browser client (`web/`) is served by **Caddy from disk** at `app.mobaide.com` — its own origin, same host. The site block reverse-proxies `/api`, `/ws`, `/relay` and `/proxy` to central-server (mirroring `web/vite.config.ts`'s dev proxy), so the SPA is fully same-origin with the relay and CORS never enters the picture. Everything else falls through to `try_files {path} /index.html` (client-side routing) with `Cache-Control: no-cache, must-revalidate` on `index.html` and unhashed public files, and `public, max-age=31536000, immutable` on Vite's content-hashed `/assets/*`.

```bash
python deploy/prod/deploy_web.py                # npm run build + ship
python deploy/prod/deploy_web.py --skip-build   # reuse existing web/dist
python deploy/prod/deploy_web.py --rollback     # repoint at previous release
```

Each deploy tars `web/dist`, extracts it to `/opt/mobaide/www/releases/<UTC-timestamp>-<sha>/`, and swaps the `current` symlink atomically (`ln -s` to a temp name, then `mv -T` — the symlink target stays **relative** so it resolves inside the caddy container's `/srv/www` bind mount). **No container restarts**; in-flight requests and live WebSockets are untouched. The newest 5 releases are kept. `--rollback` repoints at the release that was live before the last swap (each deploy records it in `www/.previous`; a second `--rollback` toggles back, and hosts without the marker fall back to the sorted predecessor of `current`). There is no auto-rollback on a failed health probe — the swap is atomic, so a failure almost always means DNS/Caddy, not release content.

First-time prerequisites, in order:

1. Create the `app.mobaide.com` DNS record — **gray cloud in both TLS modes** (see [One-time setup](#one-time-setup)).
2. Run `deploy_central.py` **without `--skip-build`** so the extract step creates `www/releases` before compose mounts `./www` (a compose-created mount would be root-owned) and Caddy picks up the new site block. This run recreates both containers — the one-time quiet window; every live WebSocket drops once and clients auto-reconnect (session replay covers the gap). Afterwards, Caddyfile-only changes ship via the deploy command's `caddy reload` step, which `stream_close_delay` makes non-disruptive.
3. Wait ~30 s (the server's ufw limits SSH to 6 connections per 30 s), then run `deploy_web.py`.

Hosts bootstrapped by the current `bootstrap-mobaide.sh` serve a placeholder page until the first web deploy; older hosts serve 404 on `/` of the app host between steps 2 and 3.

**CSP hash for the theme-boot script.** `web/index.html` carries one inline pre-paint `<script>` (theme boot), pinned in the site block's `script-src` by its `sha256-…` hash — duplicated in **both** `deploy/prod/Caddyfile.*` files. Editing that script requires recomputing the hash (build, then `deploy_web.py` prints the expected value on mismatch), updating both Caddyfiles, and shipping them via `deploy_central.py` before the next web deploy. `deploy_web.py` hard-fails on drift; the failure mode if it ever slips through is cosmetic (theme flash on load).

### Capacity & revisit triggers

Sizing targets the **Tier B (stress)** load model from the 2026-08 web-client research: ~10,000 registered users, ~2,500 DAU, ~750 peak concurrent clients ≈ **~10,000 live WebSockets** (~6 per client + 1 per agent tunnel).

- **File descriptors:** ~10k sockets ≈ 15% of the 65,536 `nofile` ceiling — never the bottleneck at this tier.
- **Memory:** <300 MiB baseline + ~450 MiB of WS state with permessage-deflate **off** (`ws_per_message_deflate=False`, pinned in `central_server/__main__.py`; deflate-on would roughly double that to ~950 MiB, which is what made the old 1 GB cap the first bottleneck) + argon2 bursts (app-bounded at 2 workers). The 4 GB cap holds the tier with headroom.
- **CPU:** peak relay fan-out (~21k frame ops/s) is 40-70% of **one** core. The relay is a single asyncio event loop — it saturates one core long before the 3.0-cpu cap matters; the spare capacity covers argon2 threads. Vertical rescaling stops helping at that point.
- **`ws_max_queue`** (uvicorn, 32 frames per socket) stays at its default — the app-level send queues (10k/5k) are the real backpressure boundary, counted by the drop counters below.

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

Three headers go on **every** response, from `central_server/central_server/security_headers.py` (a pure-ASGI middleware, registered last in `create_app` so it is the outermost of the three and stamps what the inner two produce):

| Header | Value |
| --- | --- |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Frame-Options` | `DENY` |

`X-Frame-Options` duplicates every policy's `frame-ancestors 'none'` and exists only for browsers that predate CSP framing control. `/portal/cancel-deletion` overrides the referrer policy to `no-referrer` with a document `<meta>`, which wins over the header — that page carries a cancellation token in its URL.

One gap, deliberate and worth knowing: an **unhandled** exception is turned into a 500 by Starlette's `ServerErrorMiddleware`, which wraps the entire user middleware stack, so that one response carries none of these. Everything else does, including every `HTTPException` (404 / 403 / 405) and every static file.

#### Content-Security-Policy per surface

Headers are applied with `setdefault`, so a route that sets its own value still wins. The policy is chosen by path:

| Surface | Policy |
| --- | --- |
| `/`, `/robots.txt`, `/sitemap.xml`, `/favicon.ico`, `/privacy.html` | `default-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'` |
| `/portal/*` | `default-src 'none'` + `script-src 'self' https://accounts.google.com/gsi/client` + `style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style` + `img-src`/`font-src`/`connect-src` `'self'` (+ `https://accounts.google.com/gsi/` for connect) + `frame-src https://accounts.google.com/gsi/` + `form-action 'self'` |
| `/dashboard/*` | the same shape with **no** Google origins and **no** `'unsafe-inline'` |
| everything else — `/api/*`, `/proxy/*`, `/relay/*`, `/static/*`, 404s | `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` |
| `/docs`, `/redoc` | **no CSP** — see below |
| `app.mobaide.com` static SPA (served by **Caddy**, not the app) | `default-src 'self'; script-src 'self' 'sha256-<theme-boot hash>'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` |

The SPA row is enforced by the `(app_mobaide)` snippet in both `deploy/prod/Caddyfile.*` files, because static files never pass through `security_headers.py` — the Caddy site block therefore also duplicates the three universal headers plus `Permissions-Policy`. Proxied requests on the app host (`/api`, `/ws`, `/relay`, `/proxy`) still get the app's own path-routed policies from the table above. `connect-src 'self'` covers same-origin WebSockets (CSP3 treats `ws(s)://same-host` as `'self'`).

`script-src` grants no `'unsafe-inline'` and no `'unsafe-eval'` on any surface. That is why no template contains an inline `<script>`, an `onclick=` attribute or an `hx-on:` attribute any more — the portal's behaviour lives in `static/site/portal.js`, the dashboard's in `static/dashboard.js`, and per-request values reach them through `data-` attributes. Adding any of those three forms back does not degrade a page, it silently breaks it.

`'unsafe-inline'` appears in exactly one place — `style-src` on `/portal/*`, and only when `GOOGLE_CLIENT_ID` is set. The Google Identity Services client appends an inline `<style>` to the parent document the moment it loads, on top of the `accounts.google.com/gsi/style` sheet Google documents. With no client id configured, `portal_base.html` emits no Google tag and the middleware serves the portal the strict same-origin policy instead.

`/docs` and `/redoc` are exempt on purpose. FastAPI renders both from a `cdn.jsdelivr.net` bundle with an inline bootstrap script, so any policy strict enough to be worth setting would break them, and one loose enough to fit is a list of third-party origins we do not control. Both stay unregistered unless `ENABLE_API_DOCS` is set, and admin-gated when it is. They still get the three universal headers.

#### Rolling out a CSP change — the report-only soak

Deployment is manual and a bad CSP does not trigger the `/api/health` rollback, so it can break a page while the deploy reports success. Before changing any policy:

1. Set `CSP_REPORT_ONLY=1` in `/opt/mobaide/.env` and deploy. The middleware emits `Content-Security-Policy-Report-Only` instead — violations are reported and nothing is blocked. **Four paths are the exception and stay enforced through a soak:** `/`, `/robots.txt`, `/sitemap.xml` and `/favicon.ico`. A soak is for a policy nobody has run yet; those already served this exact policy enforced from `marketing/router.py`, so de-enforcing them buys nothing and costs a live protection. Everything else soaks, `/privacy.html` included — `legal/router.py` set no security header before this middleware, so that page's policy is exactly as new as the portal's.
2. Walk the surfaces in a browser with devtools open and confirm a clean console. Cover at minimum: `/portal/login` (password reveal, Google button), `/portal/signup` (live password-match — its submit button stays disabled until JS arms, so a broken policy locks the form), `/portal/account` → delete-account modal (htmx swap, mode radios, email gate, Google re-auth), `/dashboard/users` (row expand, search swap, delete-user modal), `/privacy.html`, `/`.

   **Report-only violations do not say "Refused to …"** — that is the enforcing wording. Chrome logs them as `Loading the script '…' violates the following Content Security Policy directive: "…"`, and — this is the part that catches people — at **info** level, so a console filtered to Errors shows nothing and the page looks clean. Firefox uses `(Report-Only policy) The page's settings would block …`. Each violation also fires a `securitypolicyviolation` event on `document`, with `disposition === "report"`, which is the reliable way to script the check.

   Chrome additionally emits a one-off *"Content-Security-Policy-Report-Only … will have no effect"* warning when the policy carries no `report-uri`/`report-to`. **Ignore it.** It is about report *delivery* to a collection endpoint, not about violation visibility — per-violation console logging and the DOM event both fire regardless, which is what makes this console-based soak work. Verified empirically against this build. Server-side collection is tracked in CLA-174.
3. Remove `CSP_REPORT_ONLY` and deploy again.

The default is **enforce** (`csp_report_only: bool = False`). A report-only default would mean the policy silently protects nobody if the flag is never flipped. There is no violation-collection endpoint — the soak is browser-console-based; server-side collection is tracked in CLA-174.

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

#### WebSocket Origin allow-list

Every WS endpoint checks the `Origin` header **before** accepting the handshake (`central_server/ws_origin.py`): absent `Origin` is allowed (native apps and the agent send none — the header only means something from a browser), loopback origins are always allowed (Vite dev/preview need zero config), and anything else must match `WS_ALLOWED_ORIGINS` (comma-separated, default `https://app.mobaide.com`). Rejected handshakes close pre-accept, which uvicorn surfaces as HTTP 403, and log `ws_origin.rejected`. Relay auth rides bearer tokens rather than cookies, so this is anti-CSWSH defense in depth, not the primary control.

### Client IP behind Caddy

`docker-compose.prod.yml` sets `FORWARDED_ALLOW_IPS=*` on `central-server`. Caddy runs as a separate container, so its bridge IP — not `127.0.0.1` — is the peer uvicorn sees; at uvicorn's default the `X-Forwarded-For` header is ignored and every request is attributed to Caddy. That collapses the per-IP password-reset quota onto one shared bucket (locking out real users while doing nothing to an attacker) and fills the access log with `172.x` addresses. The wildcard is safe here only because `central-server` is `expose`-only and never published, so Caddy on the compose network is the only possible peer. Do not copy it to the dev `docker-compose.yml`, which publishes `9000:9000` directly.

### Rollback

- **Automatic**: if `docker compose up` or the `/api/health` check fails, the script swaps `central_server.prev` back in and rebuilds automatically.
- **Manual**: `deploy_central.py --rollback` does the same swap on demand (useful if a regression only surfaces later).

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

> **Requires an interactive logon.** The task only runs while the installing user has an interactive session (the lock screen is fine; a full logoff stops the task). Headless / unattended Windows hosts (build agents, CI runners, Windows servers without a logged-in user) are out of scope for this installer — there is no service-account mode. Multi-user machines: each user re-runs the installer in their own session and gets their own task pinned to themselves (no shared agent state across local accounts), mirroring Linux's `$SUDO_USER` semantics.

#### Architecture

The agent runs under a per-user Scheduled Task. The Scheduled Task action points at a **version-independent supervisor** at `<prefix>\svc\mobaide_launcher.py` so the registered task `Execute` / `-Argument` path stays valid across upgrades and rollbacks.

```
<prefix>\
├── current_version              # text file: "X.Y.Z"
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

On every task start, the supervisor (`mobaide_launcher.py`) is launched by `pythonw.exe` — the GUI-subsystem variant of CPython, which does not allocate a console window. This is what prevents the transient py.exe window from appearing at logon or during `irm install.ps1 | iex`. Because `pythonw.exe` binds its stdio to no-op handles, the supervisor redirects its own `sys.stderr` to `<prefix>\logs\launcher.err.log` at startup so diagnostics (config errors, Python tracebacks) survive. The supervisor then reads `<prefix>\current_version`, resolves the active release's `python.exe`, and spawns `python.exe -m mobaide.launcher` as a subprocess. The launcher does crash-loop detection + rollback exactly as on Linux/macOS — it rewrites `<prefix>\current_version` to a stable **complete** sibling release (one carrying the `.installed` sentinel, or the legacy `mobaide-agent.exe` console script) if the active one crashes 3 times within 60 s; an incomplete release swapped into place but not yet finalized is never selected or booted. On Windows, `mobaide.launcher` uses `subprocess.Popen` + a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` instead of `os.execv` (which creates a new PID on Windows, breaking the supervisor's `poll()` supervision — Python issue #9148). Every `subprocess.Popen` / `subprocess.run` call in the Windows agent code path also passes `creationflags=CREATE_NO_WINDOW` as defense-in-depth so child processes inherit the no-window behaviour. The agent's stderr is captured to `<prefix>\logs\agent.err.log` and its stdout (structlog records, uvicorn access logs, every `pairing_monitor.*` and `relay_client.*` event) to `<prefix>\logs\agent.out.log` (each rotated at 5 MB, one backup kept).

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
