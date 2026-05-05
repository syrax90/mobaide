#!/usr/bin/env bash
#
# Mobaide — bare-metal Linux deployment setup
#
# Usage: sudo bash deploy/setup.sh
#
# This script:
#   1. Checks prerequisites (Python 3.12+, git, curl)
#   2. Resolves the user to run the agent as (SUDO_USER, or
#      MOBAIDE_RUN_AS_USER override) — the agent must run as a real user
#      so it can read/write the developer's /home/<user> source trees.
#   3. Sets up a Python virtual environment and installs the agent
#   4. Generates an agent .env with a random JWT secret
#   5. Installs and starts the mobaide-agent systemd service
#
# The agent dials OUT to central-server via WebSocket (relay architecture),
# so no inbound tunnel / public hostname / reverse proxy is needed here.
#
set -euo pipefail

INSTALL_DIR="/opt/mobaide"
VENV_DIR="${INSTALL_DIR}/venv"
: "${MOBAIDE_RUN_AS_USER:=}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Helpers ---

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
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

resolve_run_as_user() {
    if [[ -n "$MOBAIDE_RUN_AS_USER" ]]; then
        RUN_USER="$MOBAIDE_RUN_AS_USER"
    elif [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]]; then
        RUN_USER="$SUDO_USER"
    else
        error "Could not determine which user to run the agent as."
        error "The agent must run as a real user so it can manage code under /home/<user>."
        error "Re-run with sudo (so SUDO_USER is set), or pin explicitly:"
        error "    MOBAIDE_RUN_AS_USER=alice sudo -E bash deploy/setup.sh"
        exit 1
    fi

    if ! id "$RUN_USER" >/dev/null 2>&1; then
        error "User '$RUN_USER' does not exist on this host."
        exit 1
    fi
    if [[ "$RUN_USER" == "root" ]]; then
        error "Refusing to run the agent as 'root'. Set MOBAIDE_RUN_AS_USER to a regular user."
        exit 1
    fi

    RUN_GROUP="$(id -gn "$RUN_USER")"
    RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
    if [[ -z "$RUN_HOME" ]]; then
        error "Could not resolve home directory for user '$RUN_USER'."
        exit 1
    fi
    if [[ "$RUN_HOME" == "/" ]]; then
        # Renders as `ReadWritePaths=/ <prefix>`, which neutralises
        # ProtectSystem=strict. Refuse rather than silently weaken the unit.
        error "Resolved HOME for '$RUN_USER' is '/'; refusing — agent would get writes over the entire root filesystem."
        error "Pick a regular user account: MOBAIDE_RUN_AS_USER=<user>."
        exit 1
    fi
    info "Agent will run as $RUN_USER ($RUN_GROUP), HOME=$RUN_HOME"

    if id mobaide >/dev/null 2>&1 && [[ "$RUN_USER" != "mobaide" ]]; then
        info "Removing legacy 'mobaide' system user from prior installs"
        userdel mobaide >/dev/null 2>&1 || \
            warn "Could not remove legacy 'mobaide' user (still in use?). Continuing."
    fi
}

# --- Prerequisites ---

check_root
info "Checking prerequisites..."
check_command python3
check_command git
check_command curl
check_python_version
resolve_run_as_user

# --- Directory structure ---

info "Setting up ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}/mobaide_agent"
cp -r "${REPO_ROOT}/mobaide_agent/." "${INSTALL_DIR}/mobaide_agent/"

# --- Virtual environment ---

if [[ -d "$VENV_DIR" ]]; then
    info "Virtual environment already exists at ${VENV_DIR}."
else
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

info "Installing agent package..."
"${VENV_DIR}/bin/pip" install --no-cache-dir --upgrade pip
"${VENV_DIR}/bin/pip" install --no-cache-dir "${INSTALL_DIR}/mobaide_agent"

# --- Agent .env ---

# Idempotent: re-running setup.sh never clobbers a paired agent (no --force).
info "Generating agent .env via init-env..."
"${VENV_DIR}/bin/python" -m mobaide.agent.config init-env --prefix "${INSTALL_DIR}"

# --- Permissions ---

chown -R "${RUN_USER}:${RUN_GROUP}" "$INSTALL_DIR"

# --- systemd service ---

# The repo's deploy/mobaide-agent.service is a template with sentinels;
# render it with the resolved values rather than copying it verbatim. The
# from-source layout uses INSTALL_DIR/venv directly (no releases/<v>/ split),
# so the ExecStart line is also overridden here. ProtectSystem=strict +
# scoped ReadWritePaths matches the production install model (see
# deploy/install.sh:install_systemd_unit) so dev and prod converge on
# the same hardening profile.
info "Installing systemd service..."
cat > /etc/systemd/system/mobaide-agent.service <<EOF
[Unit]
Description=Mobaide Agent (FastAPI/uvicorn)
After=network.target
Wants=network-online.target
StartLimitBurst=10
StartLimitIntervalSec=600

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=-$INSTALL_DIR/agent.env
EnvironmentFile=-$INSTALL_DIR/mobaide_agent/.env
Environment=MOBAIDE_AGENT_PREFIX=$INSTALL_DIR
Environment=HOME=$RUN_HOME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin:%h/.npm-global/bin:%h/.local/bin
ExecStart=$VENV_DIR/bin/python -m mobaide
Restart=on-failure
RestartSec=5
RestartForceExitStatus=75
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$RUN_HOME $INSTALL_DIR
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mobaide-agent
systemctl restart mobaide-agent

# --- Done ---

echo ""
echo "============================================"
echo "  Mobaide — Deployment Complete"
echo "============================================"
echo ""
echo "  Agent status:  systemctl status mobaide-agent"
echo "  Agent logs:    journalctl -u mobaide-agent -f"
echo "  Health check:  curl http://localhost:8000/api/health"
echo ""
