# Deployment Guide

Mobaide has two separately deployable services:

- **Central server** (`central_server/`) — multi-tenant relay hub. Runs on one Linux host with a public DNS name (e.g. `mobaide.com`). The Android app and every agent connect to it.
- **Mobaide agent** (`mobaide_agent/`) — per-user-machine PTY runner. Runs natively on each user's own workstation and dials OUT to the central server over an authenticated WebSocket.

The **"Production (central server on a VPS)"** section below is the recommended path for the central server. The rest of this document covers per-machine agent deployment.

## Production (central server on a VPS)

Deploy the central server only to a remote Ubuntu host (e.g. `mobaide.com`) from your Windows workstation, with a single PowerShell command — no full repo mirror on the VPS, no container registry.

### One-time setup

Follow [`deploy/prod/bootstrap-mobaide.md`](../deploy/prod/bootstrap-mobaide.md):

1. On the VPS: `mkdir /opt/mobaide`, populate `/opt/mobaide/.env` from `central_server/.env.example`, open ports 80/443.
2. DNS: `A` record for your domain → VPS public IP. Cloudflare proxy can be on (use `CloudflareOrigin` TLS mode) or off (use `LetsEncrypt`).
3. On Windows: add an `~/.ssh/config` entry so `ssh mobaide` works with key-based auth.

### Deploy

```powershell
cd D:\ML\Projects\mobaide
.\deploy\prod\deploy-central.ps1                             # LetsEncrypt (Cloudflare gray cloud)
.\deploy\prod\deploy-central.ps1 -TlsMode CloudflareOrigin   # CF Origin Cert (orange cloud)
.\deploy\prod\deploy-central.ps1 -SkipBuild                  # restart without source re-sync
.\deploy\prod\deploy-central.ps1 -Rollback                   # swap back to previous version
.\deploy\prod\deploy-central.ps1 -Force                      # allow dirty working tree
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
└── .last_tag                    # short git sha of last deploy

# Docker-managed, survive restarts:
#   central-server-data (SQLite DB), caddy-data, caddy-config
```

### How upgrades work

The deploy script tars `central_server/` with excludes (`.env`, `__pycache__`, `*.db*`, virtualenvs), `scp`s the archive to the VPS, extracts it into `central_server.new`, and **atomically swaps**: `central_server` → `central_server.prev` → `central_server.new` → `central_server`. Then `docker compose up -d --build --remove-orphans` rebuilds the image and restarts the container. The named volume `central-server-data` is never touched, so the SQLite database and Alembic migration state persist.

### Rollback

- **Automatic**: if `docker compose up` or the `/api/health` check fails, the script swaps `central_server.prev` back in and rebuilds automatically.
- **Manual**: `deploy-central.ps1 -Rollback` does the same swap on demand (useful if a regression only surfaces later).

### Verify

```powershell
curl.exe https://mobaide.com/api/health          # 200 + {"status":"ok",...}
curl.exe -I https://mobaide.com                  # cert issuer visible
ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml ps'
ssh mobaide 'docker compose -f /opt/mobaide/docker-compose.prod.yml logs --tail=60 central-server'
```

Then point the Android app (Settings → Add connection) at `https://mobaide.com`, log in with your seeded admin credentials, and pair a local agent from your workstation.

---

## Per-machine agent deployment

The agent (`mobaide_agent/`) runs natively on the user's workstation as a long-lived process that dials OUT to `central-server` over WebSocket. No container, no inbound tunnel, no public hostname, and no port-forwarding are required on the workstation — the relay architecture handles NAT traversal for you.

The Android app never talks to the agent directly; it always goes through `central-server`.

### Prerequisites

- Python 3.12+ (the production `install.ps1` flow on Windows auto-installs `Python.Python.3.12` via `winget` if no qualifying interpreter is on PATH; the conda dev path still needs it pre-installed)
- `git`, `curl`
- Claude CLI installed on the same machine (the agent spawns `claude` via PTY — see https://claude.ai/code). **v2.1.108 or newer is recommended** — the agent presets `ENABLE_PROMPT_CACHING_1H=1` on every spawn to enable the 1-hour prompt-cache TTL introduced in that release ([release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)); older CLIs will simply ignore the variable. When running as a systemd or launchd service the CLI must be on the service's `PATH` — the unit files ship with `%h/.npm-global/bin` and `%h/.local/bin` already appended. To use a non-standard install location set `MOBAIDE_CLAUDE_BIN=/absolute/path/to/claude` in `<prefix>/agent.env`.
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
Linux, `/usr/local/mobaide` on macOS), writes `<prefix>/current_version`,
and registers the supervisor:

* **Linux** — installs `mobaide-agent.service` (systemd). The unit is
  configured with `StartLimitBurst=10 / StartLimitIntervalSec=600` so the
  launcher's 3-strike rollback heuristic has room to retry a stable sibling
  release before systemd stops the unit, and `RestartForceExitStatus=75` so
  the updater's restart trigger is honoured. The agent runs as the user
  who invoked `sudo` (resolved from `$SUDO_USER`) so it can read/write
  the developer's `/home/<user>/<repo>` source trees. Override with
  `MOBAIDE_RUN_AS_USER=<user>` for CI / non-interactive provisioning.
* **macOS** — installs `~/Library/LaunchAgents/com.mobaide.agent.plist`
  (launchd). `ThrottleInterval=10` gives the boot journal the same rollback
  window on macOS. Already runs as the invoking user; no extra config.

The install script auto-generates `<prefix>/mobaide_agent/.env` with a
random `JWT_SECRET_KEY` and prefilled `RELAY_SERVER_URL=wss://mobaide.com`,
so the agent boots in **pairing-pending** state immediately. The install
script renders a pairing QR before service start; if the operator misses
it, the boot autorun re-renders, and `mobaide-agent show-qr` prints the
most-recent pending QR on demand. Scan from the Mobaide mobile app to
complete pairing — see [docs/qr-pairing.md](qr-pairing.md) for the full
flow.

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

### Threat model

The agent now runs with the developer's full UID privileges and has read/write access to `$HOME`. A compromise of the relay channel — i.e. a stolen `RELAY_AGENT_TOKEN` or a compromised central server — gains the same access the developer has interactively:

- their source trees, SSH keys, GPG keys, browser cookies, `~/.aws/credentials`, `~/.netrc`, `~/.gitconfig` (which may reference signing keys / tokens);
- any process the user can spawn (the relay can request arbitrary `command` in `create_session`);
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

The service unit at `deploy/mobaide-agent.service` is a **template** with `__RUN_USER__`, `__RUN_GROUP__`, `__RUN_HOME__` placeholders that the install scripts substitute. If you wire it up by hand, fill those in yourself; `mobaide-agent.service` is otherwise configured for the versioned `releases/` layout (`/opt/mobaide/releases/<version>/venv/` with a `current_version` pointer).

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
   script before service start, or via `mobaide-agent show-qr`). The
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
- Claude CLI not on the service's `PATH` — the agent returns HTTP 503 with `claude_cli_missing: true` and a `searched_paths` list. Fix: add `MOBAIDE_CLAUDE_BIN=/absolute/path/to/claude` to `<prefix>/agent.env`, or ensure the CLI is under `~/.npm-global/bin` or `~/.local/bin` (already in the unit's `PATH`).

#### Agent not showing as online in the Android app

Check the agent's logs for `relay.connection_failed` entries. Common causes:

- `RELAY_SERVER_URL` scheme wrong (`wss://` for HTTPS central-server, `ws://` for local plain HTTP)
- `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` don't match the bcrypt hash stored in central-server's DB for this agent — re-pair via the app or re-issue the token via the admin API
- Central-server unreachable — verify with `curl https://mobaide.com/api/health`
- For a local dev stack: the workstation's OS trust store must include Caddy's root CA (see `start-all.ps1`, which auto-installs it)
- **Cloudflare Bot Fight Mode (HTTP 403 error 1010):** if the relay is behind Cloudflare, agent v0.1.3+ sends `User-Agent: mobaide-agent/<version>` on all outbound calls (manifest polling, trust-key fetch, pairing initiate), which passes Cloudflare's default Bot Fight Mode. Operators running Super Bot Fight Mode with JA3/JA4 fingerprinting set to "Block" must add a WAF Skip rule: `(http.user_agent wildcard "mobaide-agent/*") and (http.request.uri.path starts_with "/api/")`. Older agents should be upgraded to v0.1.3+.

#### Health check failing locally

```bash
curl http://localhost:8000/api/health
# new install layout:
cat /opt/mobaide/agent.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
# legacy / from-source layout fallback:
cat /opt/mobaide/mobaide_agent/.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
```
