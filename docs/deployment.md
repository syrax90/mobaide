# Deployment Guide

Claude Mobile has two separately deployable services:

- **Central server** (`central_server/`) — multi-tenant relay hub. Runs on one Linux host with a public DNS name (e.g. `mobaide.com`). The Android app and every agent connect to it.
- **Claude agent** (`claude_agent/`) — per-user-machine PTY runner. Runs on each user's own workstation behind Cloudflare Tunnel.

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

The rest of this guide covers deploying the **agent** (`claude_agent/`) on each user's own workstation with a Cloudflare Tunnel for secure remote access.

### Prerequisites

- A **Cloudflare account** (free tier works) with a domain configured
- **One of:**
  - Docker + Docker Compose v2 (recommended)
  - Linux server with Python 3.12+, git, and curl

### Step 1: Create a Cloudflare Tunnel

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com)
2. Navigate to **Networks > Tunnels**
3. Click **Create a tunnel**, choose **Cloudflared** connector
4. Name it (e.g., `claude-mobile`), click **Save tunnel**
5. Copy the **tunnel token** — you'll need it in the next step
6. Under **Public Hostnames**, add:
   - **Subdomain**: `claude-mobile` (or your choice)
   - **Domain**: your Cloudflare domain
   - **Service type**: `HTTP`
   - **URL**: `claude-agent:8000` (Docker) or `localhost:8000` (bare metal)
7. Click **Save**

> Cloudflare natively supports WebSocket upgrade headers — no special configuration is needed for terminal streaming.

### Option A: Docker Compose (Recommended)

#### Quick Start

```bash
# Clone the repository
git clone https://github.com/syrax90/claude-mobile.git
cd claude-mobile

# Create agent .env from template
cp claude_agent/.env.example claude_agent/.env
# Edit claude_agent/.env — set JWT_SECRET_KEY (required)
# Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Start the stack with your tunnel token
TUNNEL_TOKEN=<your-token> docker compose up -d
```

#### Verify

```bash
# Check service status
docker compose ps

# Check agent health (from inside the network)
docker compose exec claude-agent curl http://localhost:8000/api/health

# Check logs
docker compose logs claude-agent
docker compose logs cloudflared

# Verify external access
curl https://claude-mobile.yourdomain.com/api/health
```

#### Create First User

```bash
docker compose exec claude-agent python -c "
import bcrypt, json, pathlib
password = bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode()
users = {'users': [{'username': 'admin', 'password_hash': password}]}
pathlib.Path('/app/data/users.json').write_text(json.dumps(users, indent=2))
print('User created.')
"
```

#### Update

```bash
git pull
docker compose build
docker compose up -d
```

#### Stop

```bash
docker compose down          # stop services
docker compose down -v       # stop and remove data volume
```

### Option B: Bare Metal (systemd + venv)

#### Automated Setup

```bash
git clone https://github.com/syrax90/claude-mobile.git
cd claude-mobile
sudo bash deploy/setup.sh
```

The script will:
- Check prerequisites (Python 3.12+, git, curl)
- Create a `claude-mobile` system user
- Set up a virtual environment at `/opt/claude-mobile/venv/`
- Install the agent package
- Generate a `.env` with a random JWT secret
- Prompt for your Cloudflare tunnel token
- Download the `cloudflared` binary
- Install and start systemd services

#### Manual Setup

If you prefer manual control:

```bash
# Create system user
sudo useradd --system --shell /usr/sbin/nologin claude-mobile

# Set up directories
sudo mkdir -p /opt/claude-mobile/claude_agent
sudo cp -r claude_agent/. /opt/claude-mobile/claude_agent/

# Create virtual environment
sudo python3 -m venv /opt/claude-mobile/venv
sudo /opt/claude-mobile/venv/bin/pip install /opt/claude-mobile/claude_agent

# Configure
sudo cp claude_agent/.env.example /opt/claude-mobile/claude_agent/.env
sudo nano /opt/claude-mobile/claude_agent/.env    # set JWT_SECRET_KEY

# Set tunnel token
echo "TUNNEL_TOKEN=<your-token>" | sudo tee /opt/claude-mobile/tunnel.env
sudo chmod 600 /opt/claude-mobile/tunnel.env

# Install cloudflared
curl -fsSL -o /usr/local/bin/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo chmod +x /usr/local/bin/cloudflared

# Install and start services
sudo cp deploy/claude-mobile-agent.service /etc/systemd/system/
sudo cp deploy/cloudflared.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-mobile-agent cloudflared

# Set ownership
sudo chown -R claude-mobile:claude-mobile /opt/claude-mobile
```

#### Service Management

```bash
# Status
systemctl status claude-mobile-agent
systemctl status cloudflared

# Logs
journalctl -u claude-mobile-agent -f
journalctl -u cloudflared -f

# Restart
sudo systemctl restart claude-mobile-agent cloudflared
```

### Android Client Configuration

1. Open the Claude Mobile app
2. Go to **Settings** (gear icon)
3. Set **Agent URL** to: `https://claude-mobile.yourdomain.com`
4. Log in with the user credentials you created above

### Optional: Cloudflare Access (Defense in Depth)

Add a second authentication layer so requests must pass Cloudflare Access before reaching the agent's JWT auth.

#### Setup

1. In Cloudflare Zero Trust, go to **Access > Applications**
2. Click **Add an application > Self-hosted**
3. Set the application domain to your tunnel hostname
4. Under **Policies**, create a policy with **Service Token** as the authentication method
5. Go to **Access > Service Tokens**, create a token
6. Note the **Client ID** and **Client Secret**

#### Android Client

When Cloudflare Access is enabled, the Android app must include these headers on every request:

```
CF-Access-Client-Id: <client-id>
CF-Access-Client-Secret: <client-secret>
```

Configure these in the app's **Settings > Cloudflare Access** section.

#### Docker Compose

No changes needed — Cloudflare Access is enforced at the Cloudflare edge, before traffic reaches the tunnel.

### Troubleshooting

#### Agent won't start

```bash
# Check logs
docker compose logs claude-agent           # Docker
journalctl -u claude-mobile-agent   # systemd

# Common causes:
# - Missing JWT_SECRET_KEY in .env
# - Port 8000 already in use
# - Python version < 3.12
```

#### Tunnel won't connect

```bash
# Check logs
docker compose logs cloudflared      # Docker
journalctl -u cloudflared            # systemd

# Common causes:
# - Invalid or expired TUNNEL_TOKEN
# - Firewall blocking outbound HTTPS (port 443)
# - DNS not yet propagated (wait a few minutes)
```

#### WebSocket not connecting from Android

- Verify the tunnel hostname is correct in the app settings
- Ensure the public hostname service URL matches:
  - Docker: `http://claude-agent:8000`
  - Bare metal: `http://localhost:8000`
- Check that the agent's health endpoint responds:
  ```bash
  curl https://claude-mobile.yourdomain.com/api/health
  ```
- If using Cloudflare Access, verify the service token headers are configured

#### Health check failing

```bash
# Docker: check if agent container is running
docker compose ps

# Test health endpoint directly
curl http://localhost:8000/api/health

# Verify .env has all required variables
cat claude_agent/.env | grep JWT_SECRET_KEY
```
