#!/usr/bin/env bash
#
# Claude Mobile — bare-metal Linux deployment setup
#
# Usage: sudo bash deploy/setup.sh
#
# This script:
#   1. Checks prerequisites (Python 3.12+, git, curl)
#   2. Creates a system user and directory structure
#   3. Sets up a Python virtual environment and installs the agent
#   4. Generates configuration files
#   5. Downloads cloudflared
#   6. Installs and starts systemd services
#
set -euo pipefail

INSTALL_DIR="/opt/claude-mobile"
VENV_DIR="${INSTALL_DIR}/venv"
SERVICE_USER="claude-mobile"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Helpers ---

info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (sudo)."
        exit 1
    fi
}

check_command() {
    if ! command -v "$1" &>/dev/null; then
        error "Required command not found: $1"
        exit 1
    fi
}

check_python_version() {
    local version
    version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    if [[ "$major" -lt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -lt 12 ]]; }; then
        error "Python 3.12+ is required (found $version)."
        exit 1
    fi
    info "Python $version detected."
}

# --- Prerequisites ---

check_root
info "Checking prerequisites..."
check_command python3
check_command git
check_command curl
check_python_version

# --- System user ---

if id "$SERVICE_USER" &>/dev/null; then
    info "User '$SERVICE_USER' already exists."
else
    info "Creating system user '$SERVICE_USER'..."
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
fi

# --- Directory structure ---

info "Setting up ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}/claude_agent"
cp -r "${REPO_ROOT}/claude_agent/." "${INSTALL_DIR}/claude_agent/"

# --- Virtual environment ---

if [[ -d "$VENV_DIR" ]]; then
    info "Virtual environment already exists at ${VENV_DIR}."
else
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

info "Installing agent package..."
"${VENV_DIR}/bin/pip" install --no-cache-dir --upgrade pip
"${VENV_DIR}/bin/pip" install --no-cache-dir "${INSTALL_DIR}/claude_agent"

# --- Agent .env ---

ENV_FILE="${INSTALL_DIR}/claude_agent/.env"
if [[ -f "$ENV_FILE" ]]; then
    info "Agent .env already exists, skipping generation."
else
    info "Generating agent .env..."
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    cat > "$ENV_FILE" <<EOF
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_MINUTES=1440
HOST=0.0.0.0
PORT=8000
MAX_SESSIONS=16
LOG_LEVEL=INFO
EOF
    info "Generated JWT_SECRET_KEY automatically."
fi

# --- Tunnel token ---

TUNNEL_ENV="${INSTALL_DIR}/tunnel.env"
if [[ -f "$TUNNEL_ENV" ]]; then
    info "tunnel.env already exists, skipping."
else
    echo ""
    echo "============================================"
    echo "  Cloudflare Tunnel Token Required"
    echo "============================================"
    echo "Create a tunnel at: https://one.dash.cloudflare.com -> Networks -> Tunnels"
    echo "Set the service URL to: http://localhost:8000"
    echo ""
    read -rp "Enter your TUNNEL_TOKEN: " TOKEN
    if [[ -z "$TOKEN" ]]; then
        error "TUNNEL_TOKEN is required."
        exit 1
    fi
    echo "TUNNEL_TOKEN=${TOKEN}" > "$TUNNEL_ENV"
    chmod 600 "$TUNNEL_ENV"
fi

# --- cloudflared binary ---

if command -v cloudflared &>/dev/null; then
    info "cloudflared is already installed."
else
    info "Downloading cloudflared..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="amd64" ;;
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm"   ;;
        *)       error "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    curl -fsSL -o /usr/local/bin/cloudflared \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}"
    chmod +x /usr/local/bin/cloudflared
    info "cloudflared installed to /usr/local/bin/cloudflared."
fi

# --- Permissions ---

chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"

# --- systemd services ---

info "Installing systemd services..."
cp "${REPO_ROOT}/deploy/claude-mobile-agent.service" /etc/systemd/system/
cp "${REPO_ROOT}/deploy/cloudflared.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable claude-mobile-agent cloudflared
systemctl start claude-mobile-agent
# Wait briefly for the agent health check before starting the tunnel
sleep 3
systemctl start cloudflared

# --- Done ---

echo ""
echo "============================================"
echo "  Claude Mobile — Deployment Complete"
echo "============================================"
echo ""
echo "  Agent status:  systemctl status claude-mobile-agent"
echo "  Tunnel status:  systemctl status cloudflared"
echo "  Agent logs:    journalctl -u claude-mobile-agent -f"
echo "  Tunnel logs:    journalctl -u cloudflared -f"
echo "  Health check:   curl http://localhost:8000/api/health"
echo ""
