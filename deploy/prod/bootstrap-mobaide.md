# One-time bootstrap for mobaide.com

Run these steps **once** before `deploy/prod/deploy_central.py` will work. Repeat only if you rebuild the VPS from scratch.

## Automated path (recommended)

Steps 1, 3, and the directory setup part of step 2 are automated by `deploy/prod/bootstrap-mobaide.sh`. Root SSH is **not** required — the script only needs to run as root *on* the VPS, which `sudo` inside the SSH session handles. From your workstation:

```bash
scp deploy/prod/bootstrap-mobaide.sh mobaide:/tmp/
ssh -t mobaide "sudo bash /tmp/bootstrap-mobaide.sh"
```

The `-t` flag forces a TTY so `sudo` can prompt for your password over the SSH wire. If your user has passwordless sudo (`NOPASSWD` in `/etc/sudoers`), plain `ssh` without `-t` works too.

The script is idempotent (safe to re-run) and:

- installs base apt packages (`curl`, `git`, `tar`, `ufw`, `cron`, `ca-certificates`),
- verifies Docker Engine + Compose plugin and adds your user to the `docker` group,
- configures ufw (default deny-in / allow-out, `limit 22/tcp`, `allow 80/tcp`, `allow 443/tcp`) and enables it,
- creates `/opt/mobaide/`, `/opt/mobaide/tls/` (0700), and `/opt/mobaide/backups/`,
- writes a skeleton `/opt/mobaide/.env` (chmod 600) with a fresh random `JWT_SECRET_KEY`.

After the script finishes you still need to:

1. Edit `/opt/mobaide/.env` on the VPS and uncomment/fill `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD`, and (if used) `GOOGLE_CLIENT_ID`.
2. Point `mobaide.com` at the VPS in Cloudflare DNS — see step 4 below for the Cloudflare proxy decision.
3. If you chose `CloudflareOrigin` TLS mode, paste the origin cert + key into `/opt/mobaide/tls/` — see step 4 option B below.
4. (Optional) add the weekly SQLite backup cron — see step 5 below.

The manual steps below remain the authoritative reference if you prefer to do everything by hand or need to understand what the script does.

## Prerequisites on the VPS

- Ubuntu 22.04 or later (or any systemd distro) with Docker Engine + Docker Compose plugin installed.
- Ports **22**, **80**, **443** reachable from the public internet.
- DNS `A` record `mobaide.com` → VPS public IP (see the TLS section below for Cloudflare proxy settings).
- DNS record for `www.mobaide.com` → same VPS (`A`, or a `CNAME` to the apex). Both Caddyfiles 308-redirect `www` → apex; without this record that site block never receives traffic.

## Prerequisites on your workstation (Windows or macOS)

- OpenSSH client (bundled with Windows 10+ and macOS) and an entry in `~/.ssh/config` that lets `ssh mobaide` connect with key-based auth:
  ```
  Host mobaide
      HostName mobaide.com
      User <your-user>
      IdentityFile ~/.ssh/id_ed25519
  ```
- `tar`, `scp`, `git` on PATH (all bundled with Windows 10+ and macOS except git, which you almost certainly have).
- Python 3.12+ (runs the deploy script, `deploy/prod/deploy_central.py`).

## 1. Prepare the server layout

```bash
ssh mobaide
sudo mkdir -p /opt/mobaide
sudo chown $USER:$USER /opt/mobaide
cd /opt/mobaide
```

## 2. Create `/opt/mobaide/.env`

Copy the template from `central_server/.env.example` in your local checkout and paste the filled-in version on the server. Required:

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

Without SMTP configured, registrations will silently fail to deliver the OTP. Set `EMAIL_VERIFICATION_REQUIRED=false` only for non-production environments without a mail provider. See [`docs/email-verification-workflow.md`](../../docs/email-verification-workflow.md) for all knobs.

If you publish agent releases via `.github/workflows/release-agent.yml`, also set:

```
RELEASE_AUTH_TOKEN=<same string as GitHub secret CENTRAL_SERVER_RELEASE_TOKEN>
MANIFEST_SIGNING_PUBLIC_KEY=<base64 Ed25519 public half of GitHub secret AGENT_MANIFEST_PRIVATE_KEY>
```

Without `RELEASE_AUTH_TOKEN`, the publish endpoint returns HTTP 503 and the workflow's "Publish manifest to central_server" step fails after `curl --retry` exhaustion. The two values **must match across GitHub and the server** — the workflow sends `CENTRAL_SERVER_RELEASE_TOKEN` in the `X-Release-Token` header, and the server compares it against `RELEASE_AUTH_TOKEN` byte-for-byte.

`MANIFEST_SIGNING_PUBLIC_KEY` is now also required for **agent-side trust-key bootstrap**. Agent install scripts (`deploy/install.sh`, `deploy/install.ps1`) auto-fetch this key from `GET /api/v1/agent/trust_key` to pin `<prefix>/trust/central_server.pub` without needing operators to set `MOBAIDE_AGENT_TRUST_KEY_B64` on every install host. Without `MANIFEST_SIGNING_PUBLIC_KEY` set on this server, that endpoint returns HTTP 503 and agent installs against this server fail-closed (which is desirable — silently shipping agents whose auto-updater can't verify rollouts is the precise bug this gate prevents).

See [`docs/versioning-and-updates.md`](../../docs/versioning-and-updates.md) for the full release-secrets table and the trust-key bootstrap flow.

All other settings (DB path, log format, etc.) can be left at their defaults — `config.py` has sane values. **JWT TTL exception:** the default TTL for all token types is now `0` (never-expires — no `exp` claim is issued). This is intentional for pre-production use. If you want tokens to expire, set one or more of the following in `.env` to a positive integer (minutes):

```
MOBILE_TOKEN_EXPIRE_MINUTES=1440      # mobile access token  (e.g. 24 h)
MOBILE_REFRESH_EXPIRE_MINUTES=10080   # mobile refresh token (e.g. 7 days)
DASHBOARD_TOKEN_EXPIRE_MINUTES=480    # dashboard token      (e.g. 8 h)
```

For the end-user web portal and account self-deletion (CLA-61), the defaults are sane but you may want to tune:

```
# Portal session token TTL (minutes); 0 = never-expires (default, same as other tokens above)
PORTAL_TOKEN_EXPIRE_MINUTES=480

# CSRF secret for cookie-authenticated portal endpoints; falls back to JWT_SECRET_KEY when unset
CSRF_SECRET_KEY=<random>

# Grace period before a scheduled deletion is permanently purged (days, default 14)
ACCOUNT_DELETION_GRACE_DAYS=14

# How often the purge sweeper runs (seconds, default 3600)
DELETION_SWEEPER_INTERVAL_SECONDS=3600

# Public base URL for cancel-deletion email links; falls back to PUBLIC_BASE_URL when unset
PORTAL_BASE_URL=https://mobaide.com
```

Capacity-hardening knob (CLA-65): ONLINE agents' heartbeats are batch-flushed to
the DB instead of written per message, so `agents.last_seen_at` for online agents
can lag by up to one flush interval (offline timestamps stay exact). Lower it for
fresher dashboard timestamps at the cost of more DB writes:

```
# How often (seconds) in-memory agent heartbeats are flushed to the DB (default 60)
HEARTBEAT_FLUSH_INTERVAL_SECONDS=60
```

The interactive OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`) are **off by
default** and return 404. Turning them on does not make the schema public — the
routes then require an admin session — but only add the knob when you actually
need them:

```
# Register /docs, /redoc and /openapi.json (admin-gated when on; default off)
ENABLE_API_DOCS=1
```

See the "Public API surface" section of [`docs/deployment.md`](../../docs/deployment.md) for the full policy.

The privacy policy is now served by the central server itself at
`/privacy.html` (rendered from `central_server/legal/privacy.md` at startup),
so there is no separate URL to configure.

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

- In Cloudflare DNS, set the `A` record for `mobaide.com` to **DNS only** (gray cloud). The `www` record must be **DNS only** too — Caddy provisions a separate cert for `www.mobaide.com` over ACME HTTP-01, which fails if Cloudflare proxies that host.
- Caddy will provision a real Let's Encrypt cert via ACME HTTP-01 on first start and renew it automatically.
- No extra files needed on the server.

### Option B — `CloudflareOrigin`

- In Cloudflare DNS, keep the `A` record **Proxied** (orange cloud). Proxy the `www` record too; the default origin-cert hostnames below (`*.mobaide.com` plus the apex) already cover `www`, so it needs no extra cert step.
- In the Cloudflare dashboard → **SSL/TLS** → **Overview**, set mode to **Full (strict)**.
- Create an Origin Certificate: **SSL/TLS** → **Origin Server** → **Create Certificate** (default RSA 2048, 15-year validity).
- Save the two PEMs on the server:
  ```bash
  mkdir -p /opt/mobaide/tls
  chmod 700 /opt/mobaide/tls
  nano /opt/mobaide/tls/origin-cert.pem   # paste certificate
  nano /opt/mobaide/tls/origin-key.pem    # paste private key
  chmod 600 /opt/mobaide/tls/origin-*.pem
  ```
- You can keep Cloudflare's Universal SSL active for the edge; CF Origin Cert covers the CF→VPS leg.

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

Back on your workstation:

```bash
cd <repo-root>
python deploy/prod/deploy_central.py                              # LetsEncrypt
# or
python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin
```

## Operational notes

- **Persistent state**: the SQLite DB lives in the named volume `central-server-data`. `docker compose up -d --build` replaces the container but keeps the volume. Never run `docker compose down -v` on the server.
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
