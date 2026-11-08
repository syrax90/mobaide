# One-time bootstrap for mobaide.com

Run these steps **once** before `deploy/prod/deploy_central.py` will work (repeat only if you rebuild the VPS).

**On this page**

- [Automated path (recommended)](#automated-path-recommended)
- [Prerequisites on the VPS](#prerequisites-on-the-vps)
- [Prerequisites on your workstation (Windows or macOS)](#prerequisites-on-your-workstation-windows-or-macos)
- [1. Prepare the server layout](#1-prepare-the-server-layout)
- [2. Create `/opt/mobaide/.env`](#2-create-optmobaideenv)
- [3. Firewall (if ufw is enabled)](#3-firewall-if-ufw-is-enabled)
- [4. Pick a TLS mode](#4-pick-a-tls-mode)
- [5. (Optional) Weekly SQLite backup](#5-optional-weekly-sqlite-backup)
- [6. First deploy](#6-first-deploy)
- [Operational notes](#operational-notes)

## Automated path (recommended)

Steps 1, 3, and the directory setup part of step 2 are automated by `deploy/prod/bootstrap-mobaide.sh`.

Root SSH is **not** required. The script only needs to run as root *on* the VPS, which `sudo` inside the SSH session handles. From your workstation:

```bash
scp deploy/prod/bootstrap-mobaide.sh mobaide:/tmp/
ssh -t mobaide "sudo bash /tmp/bootstrap-mobaide.sh"
```

The `-t` flag forces a TTY so `sudo` can prompt for your password. With passwordless sudo (`NOPASSWD`), plain `ssh` works too.

The script is idempotent and:

- installs base apt packages (`curl`, `git`, `tar`, `ufw`, `cron`, `ca-certificates`),
- verifies Docker Engine + Compose plugin and adds your user to the `docker` group,
- configures ufw (default deny-in / allow-out, `limit 22/tcp`, `allow 80/tcp`, `allow 443/tcp`) and enables it,
- creates `/opt/mobaide/`, `/opt/mobaide/tls/` (0700), `/opt/mobaide/backups/`, and `/opt/mobaide/www/` with a placeholder web release + `current` symlink,
- writes a skeleton `/opt/mobaide/.env` (chmod 600) with a fresh random `JWT_SECRET_KEY` and `PAIRING_QR_TOKEN_TTL_SECONDS=600` (web pairing links must survive a cross-device hop; the nonce is single-use, so the wider window adds no replay exposure). Servers bootstrapped before this line existed should add it by hand.

After the script finishes you still need to:

1. Edit `/opt/mobaide/.env` on the VPS and uncomment/fill `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD`, and (if used) `GOOGLE_CLIENT_ID`.
2. Point `mobaide.com` at the VPS in Cloudflare DNS (see step 4 for the proxy decision).
3. For `CloudflareOrigin` TLS mode, paste the origin cert + key into `/opt/mobaide/tls/` (step 4, option B).
4. (Optional) add the weekly SQLite backup cron (step 5).

The manual steps below are the authoritative reference.

## Prerequisites on the VPS

- Ubuntu 22.04 or later (or any systemd distro) with Docker Engine + Docker Compose plugin installed.
- Ports **22**, **80**, **443** reachable from the public internet.
- DNS `A` record `mobaide.com` → VPS public IP (see the TLS section below for Cloudflare proxy settings).
- DNS record for `www.mobaide.com` → same VPS (`A`, or a `CNAME` to the apex); both Caddyfiles 308-redirect `www` → apex.

## Prerequisites on your workstation (Windows or macOS)

- OpenSSH client and an entry in `~/.ssh/config` that lets `ssh mobaide` connect with key-based auth:
  ```
  Host mobaide
      HostName mobaide.com
      User <your-user>
      IdentityFile ~/.ssh/id_ed25519
  ```
- `tar`, `scp`, `git` on PATH.
- Python 3.12+ (runs the deploy script, `deploy/prod/deploy_central.py`).

## 1. Prepare the server layout

```bash
ssh mobaide
sudo mkdir -p /opt/mobaide
sudo chown $USER:$USER /opt/mobaide
cd /opt/mobaide
```

## 2. Create `/opt/mobaide/.env`

Copy the template from `central_server/.env.example` and paste the filled-in version on the server. Required:

```
JWT_SECRET_KEY=<random>   # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Recommended (seed a first admin so you can log in to the dashboard):

```
ADMIN_SEED_EMAIL=you@example.com
ADMIN_SEED_PASSWORD=<strong-password>
```

If you use Google-Sign-In on the Android app:

```
GOOGLE_CLIENT_ID=<your-google-client-id.apps.googleusercontent.com>
```

For email verification (enabled by default), configure SMTP so the server can send OTP codes to new users:

```
SMTP_HOST=<your-smtp-host>          # e.g. smtp.resend.com, smtp.postmarkapp.com
SMTP_PORT=465
SMTP_USERNAME=<api-key-or-user>
SMTP_PASSWORD=<secret>
SMTP_USE_TLS=true
SMTP_FROM_ADDRESS=no-reply@yourdomain.com
SMTP_FROM_NAME=Mobaide
```

Without SMTP, registrations silently fail to deliver the OTP. Set `EMAIL_VERIFICATION_REQUIRED=false` only for non-production environments. See [`docs/email-verification-workflow.md`](../../docs/email-verification-workflow.md) for all knobs.

If you publish agent releases with `deploy/prod/release_agent.py`, also set:

```
RELEASE_AUTH_TOKEN=<same string as agent/release-token in the local release key folder>
MANIFEST_SIGNING_PUBLIC_KEY=<base64 Ed25519 public half of agent/manifest-signing-key in that folder>
```

Without `RELEASE_AUTH_TOKEN`, the publish endpoint returns HTTP 503 and the script's publish step fails. The values **must match between your machine and the server**: the script sends `agent/release-token` in the `X-Release-Token` header, and the server compares it against `RELEASE_AUTH_TOKEN` byte-for-byte. The local key folder is described in [`docs/versioning-and-updates.md`](../../docs/versioning-and-updates.md#local-key-folder).

**Trust-key bootstrap.** `MANIFEST_SIGNING_PUBLIC_KEY` is also required for **agent-side trust-key bootstrap**: agent install scripts (`deploy/install.sh`, `deploy/install.ps1`) auto-fetch it from `GET /api/v1/agent/trust_key` to pin `<prefix>/trust/central_server.pub`, so operators need not set `MOBAIDE_AGENT_TRUST_KEY_B64` on every host. Without it, that endpoint returns HTTP 503 and agent installs fail closed (deliberately: agents whose auto-updater cannot verify rollouts must not ship).

See [`docs/versioning-and-updates.md`](../../docs/versioning-and-updates.md) for the local key folder and the trust-key bootstrap flow.

All other settings can be left at their `config.py` defaults. That includes the token TTLs, which are finite by default — leave them unset in production:

```
MOBILE_TOKEN_EXPIRE_MINUTES=60                 # per-agent mobile access token (1 h)
MOBILE_REFRESH_EXPIRE_MINUTES=43200            # per-agent mobile refresh token (30 days, rotates)
DASHBOARD_TOKEN_EXPIRE_MINUTES=129600          # user token of the Android/iOS apps (90 days)
ADMIN_DASHBOARD_TOKEN_EXPIRE_MINUTES=10080     # admin dashboard cookie (7 days)
```

The Android and iOS apps cannot refresh their user token, so `DASHBOARD_TOKEN_EXPIRE_MINUTES` is how often a mobile user has to sign in again (a short value such as `480` signs every phone out every 8 hours). `0` means "never expires" (no `exp` claim): a local-development opt-out only, and the server logs a `token_ttl.never_expires` warning at startup when any TTL is `0`.

For account self-deletion (CLA-61), the defaults are fine but tunable:

```
# CSRF secret for the cancel-deletion form POST; falls back to JWT_SECRET_KEY when unset
CSRF_SECRET_KEY=<random>

# Grace period before a scheduled deletion is permanently purged (days, default 14)
ACCOUNT_DELETION_GRACE_DAYS=14

# How often the purge sweeper runs (seconds, default 3600)
DELETION_SWEEPER_INTERVAL_SECONDS=3600

# Public base URL for cancel-deletion email links; falls back to PUBLIC_BASE_URL when unset
PORTAL_BASE_URL=https://mobaide.com

# Origin of the browser client every "Sign in" link on the marketing site points
# at (default https://app.mobaide.com). A malformed value falls back to that default.
WEB_APP_BASE_URL=https://app.mobaide.com
```

Capacity knob (CLA-65): online agents' heartbeats are batch-flushed, so their `agents.last_seen_at` can lag by up to one flush interval. Lower it for fresher dashboard timestamps at the cost of more DB writes:

```
# How often (seconds) in-memory agent heartbeats are flushed to the DB (default 60)
HEARTBEAT_FLUSH_INTERVAL_SECONDS=60
```

The OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`) are **off by default** (404). Turning them on keeps them admin-gated; add the knob only when needed:

```
# Register /docs, /redoc and /openapi.json (admin-gated when on; default off)
ENABLE_API_DOCS=1
```

See the "Public API surface" section of [`docs/deployment.md`](../../docs/deployment.md) for the full policy.

If you run Google Ads and want the once-per-account "Agent paired" conversion reported back to the ad click, add the six `GOOGLE_ADS_*` values (customer id, optional manager id, the Desktop-app OAuth client id and secret, the refresh token minted with `deploy/prod/google_ads_oauth.py`, the conversion action id). The upload uses the Data Manager API (no developer token) and stays off until all five required values are set. See the "Google Ads conversion upload" section of [`docs/deployment.md`](../../docs/deployment.md), including the consent-screen-must-be-In-production trap.

```
GOOGLE_ADS_CUSTOMER_ID=
GOOGLE_ADS_LOGIN_CUSTOMER_ID=
GOOGLE_ADS_OAUTH_CLIENT_ID=
GOOGLE_ADS_OAUTH_CLIENT_SECRET=
GOOGLE_ADS_OAUTH_REFRESH_TOKEN=
GOOGLE_ADS_CONVERSION_ACTION_ID=
```

```bash
nano /opt/mobaide/.env
chmod 600 /opt/mobaide/.env
```

**Never commit `.env` to any repo, and never rsync/scp it from your local machine.** The deploy script (`deploy/prod/deploy_central.py`) explicitly excludes it.

## 3. Firewall (if ufw is enabled)

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 4. Pick a TLS mode

### Option A — `LetsEncrypt` (recommended)

- In Cloudflare DNS, set these records to **DNS only** (gray cloud):
  - the `A` record for `mobaide.com`;
  - the `www` record: Caddy provisions a separate cert for `www.mobaide.com` over ACME HTTP-01, which fails if Cloudflare proxies that host;
  - the `app` record (the browser SPA host).
- Caddy provisions and renews a real Let's Encrypt cert via ACME HTTP-01 automatically; no extra files are needed on the server.

### Option B — `CloudflareOrigin`

- In Cloudflare DNS, keep the `A` record **Proxied** (orange cloud). Proxy the `www` record too, and list `www` explicitly among the origin cert's hostnames below.
- **Exception: the `app` record stays DNS only (gray cloud) even in this mode.**
  - Orange cloud would put Cloudflare's ~100 s proxy idle timeout in the path of every relay WebSocket, and a gray-cloud host cannot serve the Origin cert (browsers do not trust the Origin CA).
  - So the `app.mobaide.com` site block deliberately has no `tls` directive; Caddy ACME-issues a Let's Encrypt cert for that host over HTTP-01.
  - **That issuance happens only if the origin cert does not cover `app.mobaide.com` — see the no-wildcard step below.**
- In Cloudflare → **SSL/TLS** → **Overview**, set mode to **Full (strict)**.
- Create an Origin Certificate: **SSL/TLS** → **Origin Server** → **Create Certificate** (default RSA 2048, 15-year validity).
- **Scope it to exactly `mobaide.com`, `www.mobaide.com`, `api.mobaide.com` — delete the wildcard.** The dialog pre-fills `mobaide.com, *.mobaide.com`; leaving that wildcard in place breaks the SPA.
  - Caddy's certificate cache is global, so a `*.mobaide.com` SAN matches SNI `app.mobaide.com`. Caddy then skips ACME for it (logging `skipping automatic certificate management because one or more matching certificates are already loaded`) and serves the Origin CA cert, which no browser trusts.
  - `deploy_central.py --tls-mode CloudflareOrigin` warns when the installed cert has this problem; `deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin` reports it on demand. The `--tls-mode` flag is required — it defaults to `LetsEncrypt`, which skips the origin-cert checks entirely.
- Save the two PEMs on the server:
  ```bash
  mkdir -p /opt/mobaide/tls
  chmod 700 /opt/mobaide/tls
  nano /opt/mobaide/tls/origin-cert.pem   # paste certificate
  nano /opt/mobaide/tls/origin-key.pem    # paste private key
  chmod 600 /opt/mobaide/tls/origin-*.pem
  ```
- Verify the pair before restarting anything — the two hashes must be identical. A mismatched pair survives a `caddy reload` (the old config stays live) but bricks the **next container restart**, taking down the relay for every agent and mobile client, not just the SPA:
  ```bash
  openssl x509 -in /opt/mobaide/tls/origin-cert.pem -noout -pubkey | openssl sha256
  openssl pkey  -in /opt/mobaide/tls/origin-key.pem  -pubout | openssl sha256
  openssl x509 -in /opt/mobaide/tls/origin-cert.pem -noout -ext subjectAltName   # no wildcard here
  ```
- **Rotating the cert later:** replacing the PEMs in place and running a plain `caddy reload` does nothing (the config is unchanged, so Caddy logs `config is unchanged` and keeps the old certificate in memory). Use `--force`; `deploy_central.py` does this automatically in `CloudflareOrigin` mode:
  ```bash
  cd /opt/mobaide && docker compose -f docker-compose.prod.yml exec -T caddy \
    caddy reload --force --config /etc/caddy/Caddyfile
  ```
  Creating a new Origin Certificate does not revoke the old one, so keep a backup of the previous PEMs for rollback.
- Cloudflare's Universal SSL can stay active for the edge; the CF Origin Cert covers the CF→VPS leg.

## 5. (Optional) Weekly SQLite backup

```bash
crontab -e
```

Add:

```
0 3 * * 0 docker run --rm -v mobaide_central-server-data:/d -v /opt/mobaide/backups:/b alpine sh -c 'mkdir -p /b && cp /d/central_server.db /b/central_server.$(date +\%F).db && find /b -name "central_server.*.db" -mtime +28 -delete'
```

(Keeps 4 weekly backups in `/opt/mobaide/backups/`.)

> The DB runs in WAL mode with `synchronous=NORMAL` (CLA-65): individual commits
> are not fsynced, only checkpoints are, so an **OS crash or power loss** can lose
> the last few un-checkpointed commits. Application crashes are unaffected and the
> DB stays consistent — but on hosts without reliable power, prefer a more frequent
> backup cadence than weekly.

## 6. First deploy

```bash
cd <repo-root>
python deploy/prod/deploy_central.py                              # LetsEncrypt
# or
python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin

# then, after ~30s (ufw limits SSH to 6 connections per 30s), the web SPA:
python deploy/prod/deploy_web.py
```

`deploy_web.py` builds `web/` and ships it to `/opt/mobaide/www` with an atomic symlink swap (no container restart); see "Deploying the web SPA" in `docs/deployment.md` for the runbook and rollback.

## Operational notes

- **Persistent state**: the SQLite DB lives in the named volume `central-server-data`, which `docker compose up -d --build` keeps. Never run `docker compose down -v` on the server.
- **Rollback**: `python deploy/prod/deploy_central.py --rollback` swaps `central_server.prev` back in and rebuilds.
- **Quick restart** (no source changes): `python deploy/prod/deploy_central.py --skip-build`.
- **Manual inspection**:
  ```bash
  ssh mobaide
  cd /opt/mobaide
  docker compose -f docker-compose.prod.yml ps
  docker compose -f docker-compose.prod.yml logs --tail=100 central-server
  docker compose -f docker-compose.prod.yml logs --tail=100 caddy
  ```
