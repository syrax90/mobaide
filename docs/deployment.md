# Deployment Guide

Claude Mobile has two separately deployable services:

- **Central server** (`central_server/`) — multi-tenant relay hub. Runs on one Linux host with a public DNS name (e.g. `mobaide.com`). The Android app and every agent connect to it.
- **Claude agent** (`claude_agent/`) — per-user-machine PTY runner. Runs natively on each user's own workstation and dials OUT to the central server over an authenticated WebSocket.

The **"Production (central server on a VPS)"** section below is the recommended path for the central server. The rest of this document covers per-machine agent deployment.

## Production (central server on a VPS)

Deploy the central server only to a remote Ubuntu host (e.g. `mobaide.com`) from your Windows workstation, with a single PowerShell command — no full repo mirror on the VPS, no container registry.

### One-time setup

Follow [`deploy/prod/bootstrap-mobaide.md`](../deploy/prod/bootstrap-mobaide.md):

1. On the VPS: `mkdir /opt/claude-mobile`, populate `/opt/claude-mobile/.env` from `central_server/.env.example`, open ports 80/443.
2. DNS: `A` record for your domain → VPS public IP. Cloudflare proxy can be on (use `CloudflareOrigin` TLS mode) or off (use `LetsEncrypt`).
3. On Windows: add an `~/.ssh/config` entry so `ssh mobaide` works with key-based auth.

### Deploy

```powershell
cd D:\ML\Projects\claude_mobile
.\deploy\prod\deploy-central.ps1                             # LetsEncrypt (Cloudflare gray cloud)
.\deploy\prod\deploy-central.ps1 -TlsMode CloudflareOrigin   # CF Origin Cert (orange cloud)
.\deploy\prod\deploy-central.ps1 -SkipBuild                  # restart without source re-sync
.\deploy\prod\deploy-central.ps1 -Rollback                   # swap back to previous version
.\deploy\prod\deploy-central.ps1 -Force                      # allow dirty working tree
```

### What's on the server

```
/opt/claude-mobile/
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
ssh mobaide 'docker compose -f /opt/claude-mobile/docker-compose.prod.yml ps'
ssh mobaide 'docker compose -f /opt/claude-mobile/docker-compose.prod.yml logs --tail=60 central-server'
```

Then point the Android app (Settings → Add connection) at `https://mobaide.com`, log in with your seeded admin credentials, and pair a local agent from your workstation.

---

## Per-machine agent deployment

The agent (`claude_agent/`) runs natively on the user's workstation as a long-lived process that dials OUT to `central-server` over WebSocket. No container, no inbound tunnel, no public hostname, and no port-forwarding are required on the workstation — the relay architecture handles NAT traversal for you.

The Android app never talks to the agent directly; it always goes through `central-server`.

### Prerequisites

- Python 3.12+
- `git`, `curl`
- Claude CLI installed on the same machine (the agent spawns `claude` via PTY — see https://claude.ai/code). **v2.1.108 or newer is recommended** — the agent presets `ENABLE_PROMPT_CACHING_1H=1` on every spawn to enable the 1-hour prompt-cache TTL introduced in that release ([release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)); older CLIs will simply ignore the variable.
- A paired agent: `RELAY_AGENT_ID` + `RELAY_AGENT_TOKEN` issued by `central-server`. Pair from the Android app: **Settings → Agents → Pair new**, or via the admin REST API.

### Windows (conda — recommended for development)

```powershell
conda create -n claude-mobile python=3.12 -y
conda activate claude-mobile
pip install -e .\claude_agent

# Copy .env template and fill in the relay credentials + JWT_SECRET_KEY
Copy-Item .\claude_agent\.env.example .\claude_agent\.env
notepad .\claude_agent\.env

# Run
cd .\claude_agent
python -m claude_mobile
```

For the full local stack (Caddy + central-server in Docker + agent native), use `start-all.ps1` from the repo root.

### Linux (systemd, automated)

**End-user / production install (recommended):** pull a signed release from `central_server` via the install script:

```bash
curl -fsSL https://get.claudemobile.io/install.sh | sudo bash
```

The script verifies the Ed25519 manifest signature, stages the release under `/opt/claude-mobile/releases/<version>/venv/`, writes `/opt/claude-mobile/current_version`, and installs `claude-mobile-agent.service`.

After install, edit `/opt/claude-mobile/agent.env` (created by the script) and add the relay credentials (`RELAY_CENTRAL_URL`, `RELAY_AGENT_ID`, `RELAY_AGENT_TOKEN`), then `sudo systemctl restart claude-mobile-agent`.

**From-source / developer install:** use `deploy/setup.sh` (sets up a flat venv from the local repo checkout). Note that `setup.sh` installs the same service unit, which now expects the versioned `releases/` layout; use the end-user script above on production hosts.

```bash
sudo bash deploy/setup.sh
```

### Linux (systemd, manual)

The service unit (`deploy/claude-mobile-agent.service`) now expects the versioned release layout:
`/opt/claude-mobile/releases/<version>/venv/` with a `current_version` pointer file.
The steps below reflect that layout.

```bash
VERSION="0.1.0"   # replace with the actual agent version being installed

sudo useradd --system --shell /usr/sbin/nologin claude-mobile

sudo mkdir -p /opt/claude-mobile/releases/${VERSION}
sudo cp -r claude_agent/. /opt/claude-mobile/releases/${VERSION}/

sudo python3 -m venv /opt/claude-mobile/releases/${VERSION}/venv
sudo /opt/claude-mobile/releases/${VERSION}/venv/bin/pip install /opt/claude-mobile/releases/${VERSION}

echo "${VERSION}" | sudo tee /opt/claude-mobile/current_version

sudo cp claude_agent/.env.example /opt/claude-mobile/agent.env
sudoedit /opt/claude-mobile/agent.env   # set JWT_SECRET_KEY and RELAY_* vars

sudo cp deploy/claude-mobile-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-mobile-agent

sudo chown -R claude-mobile:claude-mobile /opt/claude-mobile
```

### Service management

```bash
systemctl status claude-mobile-agent
journalctl -u claude-mobile-agent -f
sudo systemctl restart claude-mobile-agent
```

### Android client configuration

1. Open the Claude Mobile app
2. Go to **Settings → Add connection**
3. Set the **Server URL** to your central-server base URL, e.g. `https://mobaide.com`
4. Sign in (or sign up), then pair this agent under **Settings → Agents → Pair new** and copy the generated `RELAY_AGENT_ID` + `RELAY_AGENT_TOKEN` into the agent's `.env`
5. Restart the agent — it will show up as online in the app within a few seconds

### Troubleshooting

#### Agent won't start

```bash
journalctl -u claude-mobile-agent -n 100
```

Common causes:

- Missing `JWT_SECRET_KEY` in `.env`
- Port 8000 already in use (change `PORT`, or stop the other process)
- Python version < 3.12

#### Agent not showing as online in the Android app

Check the agent's logs for `relay.connection_failed` entries. Common causes:

- `RELAY_CENTRAL_URL` scheme wrong (`wss://` for HTTPS central-server, `ws://` for local plain HTTP)
- `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` don't match the bcrypt hash stored in central-server's DB for this agent — re-pair via the app or re-issue the token via the admin API
- Central-server unreachable — verify with `curl https://mobaide.com/api/health`
- For a local dev stack: the workstation's OS trust store must include Caddy's root CA (see `start-all.ps1`, which auto-installs it)

#### Health check failing locally

```bash
curl http://localhost:8000/api/health
# new install layout:
cat /opt/claude-mobile/agent.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
# legacy / from-source layout fallback:
cat /opt/claude-mobile/claude_agent/.env | grep -E '^(JWT_SECRET_KEY|RELAY_)'
```
