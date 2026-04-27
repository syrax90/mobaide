# One-time bootstrap for mobaide.com

Run these steps **once** before `deploy-central.ps1` will work. Repeat only if you rebuild the VPS from scratch.

## Automated path (recommended)

Steps 1, 3, and the directory setup part of step 2 are automated by `deploy/prod/bootstrap-mobaide.sh`. Root SSH is **not** required — the script only needs to run as root *on* the VPS, which `sudo` inside the SSH session handles. From your Windows workstation:

```powershell
scp deploy\prod\bootstrap-mobaide.sh mobaide:/tmp/
ssh -t mobaide "sudo bash /tmp/bootstrap-mobaide.sh"
```

The `-t` flag forces a TTY so `sudo` can prompt for your password over the SSH wire. If your user has passwordless sudo (`NOPASSWD` in `/etc/sudoers`), plain `ssh` without `-t` works too.

The script is idempotent (safe to re-run) and:

- installs base apt packages (`curl`, `git`, `tar`, `ufw`, `cron`, `ca-certificates`),
- verifies Docker Engine + Compose plugin and adds your user to the `docker` group,
- configures ufw (default deny-in / allow-out, `limit 22/tcp`, `allow 80/tcp`, `allow 443/tcp`) and enables it,
- creates `/opt/claude-mobile/`, `/opt/claude-mobile/tls/` (0700), and `/opt/claude-mobile/backups/`,
- writes a skeleton `/opt/claude-mobile/.env` (chmod 600) with a fresh random `JWT_SECRET_KEY`.

After the script finishes you still need to:

1. Edit `/opt/claude-mobile/.env` on the VPS and uncomment/fill `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD`, and (if used) `GOOGLE_CLIENT_ID`.
2. Point `mobaide.com` at the VPS in Cloudflare DNS — see step 4 below for the Cloudflare proxy decision.
3. If you chose `CloudflareOrigin` TLS mode, paste the origin cert + key into `/opt/claude-mobile/tls/` — see step 4 option B below.
4. (Optional) add the weekly SQLite backup cron — see step 5 below.

The manual steps below remain the authoritative reference if you prefer to do everything by hand or need to understand what the script does.

## Prerequisites on the VPS

- Ubuntu 22.04 or later (or any systemd distro) with Docker Engine + Docker Compose plugin installed.
- Ports **22**, **80**, **443** reachable from the public internet.
- DNS `A` record `mobaide.com` → VPS public IP (see the TLS section below for Cloudflare proxy settings).

## Prerequisites on your Windows workstation

- OpenSSH client (bundled with Windows 10+) and an entry in `~/.ssh/config` that lets `ssh mobaide` connect with key-based auth:
  ```
  Host mobaide
      HostName mobaide.com
      User <your-user>
      IdentityFile ~/.ssh/id_ed25519
  ```
- `tar`, `scp`, `git` on PATH (all bundled with Windows 10+ except git, which you almost certainly have).

## 1. Prepare the server layout

```bash
ssh mobaide
sudo mkdir -p /opt/claude-mobile
sudo chown $USER:$USER /opt/claude-mobile
cd /opt/claude-mobile
```

## 2. Create `/opt/claude-mobile/.env`

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

All other settings (DB path, log format, etc.) can be left at their defaults — `config.py` has sane values. **JWT TTL exception:** the default TTL for all token types is now `0` (never-expires — no `exp` claim is issued). This is intentional for pre-production use. If you want tokens to expire, set one or more of the following in `.env` to a positive integer (minutes):

```
MOBILE_TOKEN_EXPIRE_MINUTES=1440      # mobile access token  (e.g. 24 h)
MOBILE_REFRESH_EXPIRE_MINUTES=10080   # mobile refresh token (e.g. 7 days)
DASHBOARD_TOKEN_EXPIRE_MINUTES=480    # dashboard token      (e.g. 8 h)
```

```bash
nano /opt/claude-mobile/.env
chmod 600 /opt/claude-mobile/.env
```

**Never commit `.env` to any repo, and never rsync/scp it from your local machine.** The PowerShell deploy script explicitly excludes it.

## 3. Firewall (if ufw is enabled)

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 4. Pick a TLS mode

### Option A — `LetsEncrypt` (recommended)

- In Cloudflare DNS, set the `A` record for `mobaide.com` to **DNS only** (gray cloud).
- Caddy will provision a real Let's Encrypt cert via ACME HTTP-01 on first start and renew it automatically.
- No extra files needed on the server.

### Option B — `CloudflareOrigin`

- In Cloudflare DNS, keep the `A` record **Proxied** (orange cloud).
- In the Cloudflare dashboard → **SSL/TLS** → **Overview**, set mode to **Full (strict)**.
- Create an Origin Certificate: **SSL/TLS** → **Origin Server** → **Create Certificate** (default RSA 2048, 15-year validity).
- Save the two PEMs on the server:
  ```bash
  mkdir -p /opt/claude-mobile/tls
  chmod 700 /opt/claude-mobile/tls
  nano /opt/claude-mobile/tls/origin-cert.pem   # paste certificate
  nano /opt/claude-mobile/tls/origin-key.pem    # paste private key
  chmod 600 /opt/claude-mobile/tls/origin-*.pem
  ```
- You can keep Cloudflare's Universal SSL active for the edge; CF Origin Cert covers the CF→VPS leg.

## 5. (Optional) Weekly SQLite backup

```bash
crontab -e
```

Add:

```
0 3 * * 0 docker run --rm -v claude-mobile_central-server-data:/d -v /opt/claude-mobile/backups:/b alpine sh -c 'mkdir -p /b && cp /d/central_server.db /b/central_server.$(date +\%F).db && find /b -name "central_server.*.db" -mtime +28 -delete'
```

(Keeps 4 weekly backups in `/opt/claude-mobile/backups/`.)

## 6. First deploy

Back on Windows:

```powershell
cd D:\ML\Projects\claude_mobile
.\deploy\prod\deploy-central.ps1                             # LetsEncrypt
# or
.\deploy\prod\deploy-central.ps1 -TlsMode CloudflareOrigin
```

## Operational notes

- **Persistent state**: the SQLite DB lives in the named volume `central-server-data`. `docker compose up -d --build` replaces the container but keeps the volume. Never run `docker compose down -v` on the server.
- **Rollback**: `.\deploy\prod\deploy-central.ps1 -Rollback` swaps `central_server.prev` back in and rebuilds.
- **Quick restart** (no source changes): `.\deploy\prod\deploy-central.ps1 -SkipBuild`.
- **Manual inspection**:
  ```bash
  ssh mobaide
  cd /opt/claude-mobile
  docker compose -f docker-compose.prod.yml ps
  docker compose -f docker-compose.prod.yml logs --tail=100 central-server
  docker compose -f docker-compose.prod.yml logs --tail=100 caddy
  ```
