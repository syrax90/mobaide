#!/usr/bin/env bash
#
# Mobaide — bare-metal Linux deployment setup
#
# Usage: sudo bash deploy/setup.sh
#
# This script:
#   1. Checks prerequisites (Python 3.12+, git, curl)
#   2. Creates a system user and directory structure
#   3. Sets up a Python virtual environment and installs the agent
#   4. Generates an agent .env with a random JWT secret
#   5. Installs and starts the claude-mobile-agent systemd service
#
# The agent dials OUT to central-server via WebSocket (relay architecture),
# so no inbound tunnel / public hostname / reverse proxy is needed here.
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
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=0
JWT_REFRESH_TOKEN_EXPIRE_MINUTES=0
HOST=0.0.0.0
PORT=8000
MAX_SESSIONS=16
LOG_LEVEL=INFO
EOF
    info "Generated JWT_SECRET_KEY automatically."
    info "NOTE: add RELAY_CENTRAL_URL, RELAY_AGENT_ID, RELAY_AGENT_TOKEN to ${ENV_FILE}"
    info "      after pairing this agent with central-server."
fi

# --- Permissions ---

chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"

# --- systemd service ---

info "Installing systemd service..."
cp "${REPO_ROOT}/deploy/claude-mobile-agent.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable claude-mobile-agent
systemctl start claude-mobile-agent

# --- Done ---

echo ""
echo "============================================"
echo "  Mobaide — Deployment Complete"
echo "============================================"
echo ""
echo "  Agent status:  systemctl status claude-mobile-agent"
echo "  Agent logs:    journalctl -u claude-mobile-agent -f"
echo "  Health check:  curl http://localhost:8000/api/health"
echo ""
