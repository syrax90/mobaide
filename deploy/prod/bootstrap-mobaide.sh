#!/usr/bin/env bash
#
# Mobaide - one-time VPS bootstrap for mobaide.com
#
# Usage (from your Windows workstation, root SSH is not required):
#     scp deploy/prod/bootstrap-mobaide.sh mobaide:/tmp/
#     ssh -t mobaide "sudo bash /tmp/bootstrap-mobaide.sh"
#
# The `-t` forces a TTY so `sudo` can prompt for your password. If the
# invoking user has NOPASSWD sudo, plain `ssh` without `-t` also works.
#
# Or directly on the VPS:
#     sudo bash bootstrap-mobaide.sh
#
# Safe to re-run; every step checks existing state before acting.
#
# What this does:
#   1. Installs base apt packages (curl, git, tar, ufw, cron, ca-certificates).
#   2. Verifies Docker Engine + Compose plugin are usable.
#   3. Adds the invoking (sudo) user to the `docker` group for scp/ssh deploys.
#   4. Configures ufw: default deny-in/allow-out, limit 22/tcp, allow 80/tcp+443/tcp.
#   5. Creates /opt/mobaide/{,tls,backups,www/releases} owned by the deploy
#      user, plus a placeholder web release so app.mobaide.com serves a page
#      before the first deploy_web.py run.
#   6. Generates a skeleton /opt/mobaide/.env with a random JWT_SECRET_KEY.
#
# What this does NOT do:
#   - Install Docker (use `curl -fsSL https://get.docker.com | sh` if missing).
#   - Modify /etc/ssh/sshd_config.
#   - Install the weekly SQLite backup cron (see bootstrap-mobaide.md step 5).
#   - Write TLS cert files or deploy the service (deploy_central.py does that).
#
set -euo pipefail

INSTALL_DIR="/opt/mobaide"
ENV_FILE="${INSTALL_DIR}/.env"

# --- Helpers ---

info()    { echo "[INFO]  $*"; }
ok()      { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
err()     { echo "[ERROR] $*" >&2; }
section() { echo; echo "=== $* ==="; }

# --- 1. Preflight ---

section "1/6  Preflight"

if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root. Try: sudo bash $0"
    exit 1
fi

# Use the non-root invoker so /opt/mobaide is writable by them over
# ssh/scp. Fall back to root only if run directly as root.
DEPLOY_USER="${SUDO_USER:-}"
if [[ -z "$DEPLOY_USER" || "$DEPLOY_USER" == "root" ]]; then
    warn "SUDO_USER unset; /opt/mobaide will be owned by root."
    warn "  Deploys over ssh will need sudo unless you chown it to a user later."
    DEPLOY_USER="root"
    DEPLOY_GROUP="root"
else
    DEPLOY_GROUP="$(id -gn "$DEPLOY_USER")"
    ok "Deploy user detected: ${DEPLOY_USER}:${DEPLOY_GROUP}"
fi

if ! command -v apt-get &>/dev/null; then
    err "apt-get not found; this script targets Ubuntu/Debian hosts only."
    exit 1
fi
ok "apt-get present (Ubuntu/Debian host)"

# --- 2. Base packages ---

section "2/6  Base packages"

export DEBIAN_FRONTEND=noninteractive

# Refresh the apt cache at most once per day to keep reruns snappy.
CACHE_FILE="/var/cache/apt/pkgcache.bin"
if [[ ! -f "$CACHE_FILE" ]] || [[ -z "$(find "$CACHE_FILE" -mmin -1440 2>/dev/null)" ]]; then
    info "Running apt-get update..."
    apt-get update -y
else
    info "apt cache is <24h old; skipping apt-get update."
fi

info "Ensuring base packages are installed..."
apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    tar \
    ufw \
    cron

ok "Base packages present"

# --- 3. Docker sanity check ---

section "3/6  Docker"

if ! command -v docker &>/dev/null; then
    err "docker binary not found. Install it with:"
    err "    curl -fsSL https://get.docker.com | sh"
    exit 1
fi
if ! docker compose version &>/dev/null; then
    err "docker compose plugin not found. Install docker-compose-plugin."
    exit 1
fi
ok "Docker Engine + Compose plugin present: $(docker --version)"

if ! systemctl is-active --quiet docker; then
    info "Starting docker daemon..."
    systemctl start docker
fi
systemctl enable docker &>/dev/null || true
ok "docker daemon running and enabled"

if [[ "$DEPLOY_USER" != "root" ]]; then
    if id -nG "$DEPLOY_USER" | tr ' ' '\n' | grep -qx docker; then
        ok "$DEPLOY_USER is already in the docker group"
    else
        info "Adding $DEPLOY_USER to the docker group..."
        usermod -aG docker "$DEPLOY_USER"
        warn "Group membership takes effect on next login for $DEPLOY_USER."
    fi
fi

# --- 4. Firewall (ufw) ---

section "4/6  Firewall (ufw)"

ufw default deny incoming  >/dev/null
ufw default allow outgoing >/dev/null
ok "ufw defaults: deny incoming, allow outgoing"

add_rule() {
    # $1 = human label, $2 = ufw verb (allow|limit), $3 = port/proto
    local label="$1" verb="$2" rule="$3"
    if ufw status 2>/dev/null | grep -q "^${rule} "; then
        info "ufw already has a rule for $rule ($label); skipping."
    else
        info "ufw $verb $rule  # $label"
        ufw "$verb" "$rule" >/dev/null
    fi
}

add_rule "SSH (rate-limited)"             limit 22/tcp
add_rule "HTTP (Caddy ACME + redirect)"   allow 80/tcp
add_rule "HTTPS (Caddy)"                  allow 443/tcp

if ufw status | grep -q "Status: active"; then
    ok "ufw already active"
else
    info "Enabling ufw..."
    ufw --force enable >/dev/null
    ok "ufw enabled"
fi

# --- 5. Directory layout ---

section "5/6  Directory layout under $INSTALL_DIR"

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/tls" "$INSTALL_DIR/backups"
chown "${DEPLOY_USER}:${DEPLOY_GROUP}" "$INSTALL_DIR" "$INSTALL_DIR/tls" "$INSTALL_DIR/backups"
chmod 0755 "$INSTALL_DIR" "$INSTALL_DIR/backups"
chmod 0700 "$INSTALL_DIR/tls"
ok "$INSTALL_DIR, $INSTALL_DIR/tls (0700), $INSTALL_DIR/backups ready"

# Web SPA releases (served by Caddy at app.mobaide.com; deployed by
# deploy_web.py as www/releases/<tag>/ + an atomic `current` symlink).
# Creating www/ here — before docker compose ever mounts it — keeps it
# owned by the deploy user; if compose mounted a missing path first,
# dockerd would create it root-owned and deploy_web.py would hit
# permission errors. The placeholder release means the site block serves
# a real page (not a 404) between the Caddy deploy and the first web
# deploy; its tag sorts before any timestamped release, so the normal
# keep-last-5 pruning retires it naturally.
WWW_DIR="$INSTALL_DIR/www"
PLACEHOLDER_REL="releases/00000000T000000Z-bootstrap"
mkdir -p "$WWW_DIR/$PLACEHOLDER_REL"
if [[ ! -f "$WWW_DIR/$PLACEHOLDER_REL/index.html" ]]; then
    # Bare HTML on purpose: the production CSP allows only 'self'
    # stylesheets and one hashed inline script, so no <style>/<script>.
    cat > "$WWW_DIR/$PLACEHOLDER_REL/index.html" <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Mobaide</title>
  </head>
  <body>
    <h1>Mobaide</h1>
    <p>The web app has not been deployed to this server yet.
       Run <code>python deploy/prod/deploy_web.py</code> from your workstation.</p>
  </body>
</html>
EOF
fi
# -L guard as well as -e: -e alone is false for a dangling symlink, and a
# bare `ln -s` onto one would fail with EEXIST.
if [[ ! -e "$WWW_DIR/current" && ! -L "$WWW_DIR/current" ]]; then
    # RELATIVE target — must resolve inside the caddy container's /srv/www
    # bind mount, where $INSTALL_DIR does not exist.
    ln -s "$PLACEHOLDER_REL" "$WWW_DIR/current"
fi
chown -R "${DEPLOY_USER}:${DEPLOY_GROUP}" "$WWW_DIR"
chmod 0755 "$WWW_DIR"
ok "$WWW_DIR ready (placeholder release + current symlink)"

# --- 6. /opt/mobaide/.env skeleton ---

section "6/6  Environment file ($ENV_FILE)"

if [[ -f "$ENV_FILE" ]]; then
    ok ".env already present; leaving it alone."
else
    info "Generating skeleton .env..."
    if command -v python3 &>/dev/null; then
        JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    else
        JWT_SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '\n=+/' | cut -c1-43)"
    fi
    cat > "$ENV_FILE" <<EOF
# /opt/mobaide/.env - secrets for central_server (chmod 600, NEVER commit).
# Generated by bootstrap-mobaide.sh on $(date -Iseconds).

JWT_SECRET_KEY=${JWT_SECRET}

# Seed a first admin so you can log in to the dashboard.
# Uncomment and fill in before running deploy_central.py.
# ADMIN_SEED_EMAIL=you@example.com
# ADMIN_SEED_PASSWORD=change-me

# Required only if you use Google Sign-In (Android app, iOS app, web client).
# GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com

# Required only if you use Sign in with Apple. APPLE_CLIENT_ID is the iOS
# bundle id; APPLE_WEB_CLIENT_ID is the Services ID the browser flow needs,
# and it must be grouped under APPLE_CLIENT_ID as its primary App ID or web
# sign-ins create duplicate accounts. See docs/deployment.md.
# APPLE_CLIENT_ID=com.mobaide.app
# APPLE_WEB_CLIENT_ID=com.mobaide.web

# Required only if you publish agent releases via release-agent.yml.
# Without these, POST /api/internal/agent_manifest returns HTTP 503 and
# the release workflow fails. RELEASE_AUTH_TOKEN must hold the SAME
# string as the GitHub Actions secret CENTRAL_SERVER_RELEASE_TOKEN.
# MANIFEST_SIGNING_PUBLIC_KEY must be the public half of the GitHub
# Actions secret AGENT_MANIFEST_PRIVATE_KEY (base64 Ed25519).
# RELEASE_AUTH_TOKEN=
# MANIFEST_SIGNING_PUBLIC_KEY=

# Web pairing links: the QR pairing nonce must survive the human hop from
# a terminal QR on one machine to a browser on another (copy link, switch
# device, log in, open). 600s closes that race; the nonce stays
# single-use, so the wider window adds no replay exposure. Code default
# is 120s (scan-with-phone flow).
PAIRING_QR_TOKEN_TTL_SECONDS=600

# HOST, PORT, DB_PATH are set by docker-compose.prod.yml - do not duplicate here.
EOF
    chown "${DEPLOY_USER}:${DEPLOY_GROUP}" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    ok "Wrote $ENV_FILE (0600) with a fresh JWT_SECRET_KEY"
    warn "Edit $ENV_FILE and fill in the admin seed + Google client id before deploying."
fi

# --- Summary ---

section "Summary"

echo
echo "Firewall:"
ufw status verbose | sed 's/^/  /'
echo
echo "Docker:"
echo "  $(docker --version)"
echo "  $(docker compose version)"
echo
echo "Install dir:"
ls -la "$INSTALL_DIR" | sed 's/^/  /'
echo
ok "Bootstrap complete. Next, on your workstation:"
echo "      python deploy/prod/deploy_central.py"
echo "      # then, ~30s later (ufw SSH rate limit), the web SPA:"
echo "      python deploy/prod/deploy_web.py"
echo
