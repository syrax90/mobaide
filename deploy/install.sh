#!/usr/bin/env bash
#
# Mobaide agent install script (Linux + macOS).
#
# Usage (curl|bash):
#     curl -fsSL https://get.mobaide.com/install.sh | bash
#
# Idempotent: re-running upgrades the agent in place. Each release is
# staged into a sibling ``releases/<version>/`` directory and the
# ``current_version`` pointer is rewritten atomically; the launcher boots
# into whichever release is current.
#
# Wrap everything in main() and call it once at the bottom so a partial
# download (curl|sh) cannot execute half a script.

set -euo pipefail

# Defaults (override via env var).
: "${MOBAIDE_AGENT_PREFIX:=}"
: "${MOBAIDE_AGENT_MANIFEST_URL:=https://get.mobaide.com/manifest.json}"
: "${MOBAIDE_AGENT_TRUST_KEY_B64:=}"
: "${MOBAIDE_AGENT_SERVICE_USER:=mobaide}"
: "${MOBAIDE_AGENT_INSTALL_URL:=https://get.mobaide.com/install.sh}"

info()  { printf '[INFO]  %s\n' "$*"; }
warn()  { printf '[WARN]  %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
fail()  { error "$*"; exit 1; }

detect_platform() {
    case "$(uname -s)" in
        Linux)  PLATFORM="linux" ;;
        Darwin) PLATFORM="darwin" ;;
        *)      fail "Unsupported OS: $(uname -s). Linux and macOS only." ;;
    esac

    if [ -z "$MOBAIDE_AGENT_PREFIX" ]; then
        if [ "$PLATFORM" = "linux" ]; then
            MOBAIDE_AGENT_PREFIX="/opt/mobaide"
        else
            MOBAIDE_AGENT_PREFIX="/usr/local/mobaide"
        fi
    fi
}

require_root_when_writing_system_dir() {
    case "$MOBAIDE_AGENT_PREFIX" in
        /opt/*|/usr/*|/etc/*) ;;
        *) return 0 ;;
    esac
    [ "$(id -u)" -eq 0 ] && return 0

    # `sudo curl ... | bash` only elevates curl, not bash, so this branch is
    # the common case rather than an edge case. Re-fetch the script under
    # sudo so the canonical one-liner Just Works.
    if command -v sudo >/dev/null 2>&1; then
        info "Installing to $MOBAIDE_AGENT_PREFIX requires root — re-executing under sudo."
        info "(With 'curl|bash', sudo applies only to curl; auto-elevating now.)"
        exec sudo \
            --preserve-env=MOBAIDE_AGENT_PREFIX,MOBAIDE_AGENT_MANIFEST_URL,MOBAIDE_AGENT_TRUST_KEY_B64,MOBAIDE_AGENT_SERVICE_USER,MOBAIDE_AGENT_INSTALL_URL \
            bash -c 'curl -fsSL "$1" | bash' _ "$MOBAIDE_AGENT_INSTALL_URL"
    fi

    fail "Installing to $MOBAIDE_AGENT_PREFIX requires root, and sudo is not available.
Re-run as root, or set MOBAIDE_AGENT_PREFIX to a user-writable directory:
    MOBAIDE_AGENT_PREFIX=\$HOME/.mobaide curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | bash"
}

check_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 is required (>= 3.12)."
    fi
    local version major minor
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        fail "Python 3.12+ is required (found $version)."
    fi
    info "Python $version detected."
}

fetch_manifest() {
    local manifest_path="$1"
    info "Fetching manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    if ! curl -fsSL --max-time 30 "$MOBAIDE_AGENT_MANIFEST_URL" -o "$manifest_path"; then
        fail "Could not fetch manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    fi
}

verify_manifest_signature() {
    local manifest_path="$1"
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        warn "MOBAIDE_AGENT_TRUST_KEY_B64 not set — skipping manifest signature check."
        warn "First-install bootstrap; pin the public key in production install."
        return 0
    fi
    info "Verifying manifest signature"
    python3 - "$manifest_path" "$MOBAIDE_AGENT_TRUST_KEY_B64" <<'PY'
import base64, json, sys
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

manifest_path, public_key_b64 = sys.argv[1:3]
record = json.loads(Path(manifest_path).read_text())
body, sig = record["body"], record["signature_b64"]
canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
        base64.b64decode(sig), canon
    )
except InvalidSignature:
    sys.exit("manifest signature does not verify")
PY
}

stage_release() {
    local manifest_path="$1"
    local target_version
    target_version="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['body']['version'])" "$manifest_path")"
    if [ -z "$target_version" ]; then
        fail "manifest is missing 'version'"
    fi

    local release_dir="${MOBAIDE_AGENT_PREFIX}/releases/${target_version}"
    info "Staging release $target_version into $release_dir"

    mkdir -p "$release_dir"
    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "$tmpdir"' EXIT

    python3 - "$manifest_path" "$tmpdir" "$release_dir" <<'PY'
import hashlib, json, shutil, sys, tarfile, urllib.request
from pathlib import Path

manifest_path, tmpdir, release_dir = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body = record["body"]

def fetch(url, dest):
    if url.startswith("file://") or url.startswith("/"):
        shutil.copyfile(url.removeprefix("file://"), dest)
        return
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)

def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        sys.exit(f"sha256 mismatch for {path}")

tarball = Path(tmpdir) / "agent.tar.gz"
wheel = Path(tmpdir) / "agent.whl"
fetch(body["tarball_url"], tarball)
verify_sha256(tarball, body["tarball_sha256"])
fetch(body["wheel_url"], wheel)
verify_sha256(wheel, body["wheel_sha256"])

with tarfile.open(tarball, mode="r:gz") as tar:
    tar.extractall(release_dir, filter="data")

print(wheel)
PY

    local wheel_path
    wheel_path="$(ls "$tmpdir"/agent.whl)"

    info "Creating venv in $release_dir/venv"
    python3 -m venv "$release_dir/venv"
    "$release_dir/venv/bin/pip" install --no-cache-dir --upgrade pip
    "$release_dir/venv/bin/pip" install --no-cache-dir "$wheel_path"

    rm -rf "$tmpdir"
    trap - EXIT

    printf '%s\n' "$target_version" > "${MOBAIDE_AGENT_PREFIX}/current_version.tmp"
    mv "${MOBAIDE_AGENT_PREFIX}/current_version.tmp" "${MOBAIDE_AGENT_PREFIX}/current_version"
    info "Pinned current_version=$target_version"
}

write_trust_key() {
    if [ -n "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        mkdir -p "${MOBAIDE_AGENT_PREFIX}/trust"
        printf '%s\n' "$MOBAIDE_AGENT_TRUST_KEY_B64" > "${MOBAIDE_AGENT_PREFIX}/trust/central_server.pub"
    fi
}

ensure_service_user() {
    if [ "$PLATFORM" != "linux" ]; then return 0; fi
    if id "$MOBAIDE_AGENT_SERVICE_USER" >/dev/null 2>&1; then return 0; fi
    info "Creating system user $MOBAIDE_AGENT_SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$MOBAIDE_AGENT_PREFIX" "$MOBAIDE_AGENT_SERVICE_USER"
}

install_systemd_unit() {
    local unit="/etc/systemd/system/mobaide-agent.service"
    info "Installing systemd unit at $unit"
    cat > "$unit" <<EOF
[Unit]
Description=Mobaide Agent (FastAPI/uvicorn)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$MOBAIDE_AGENT_SERVICE_USER
Group=$MOBAIDE_AGENT_SERVICE_USER
EnvironmentFile=-$MOBAIDE_AGENT_PREFIX/agent.env
Environment=MOBAIDE_AGENT_PREFIX=$MOBAIDE_AGENT_PREFIX
ExecStart=$MOBAIDE_AGENT_PREFIX/releases/\$(cat $MOBAIDE_AGENT_PREFIX/current_version)/venv/bin/python -m mobaide.launcher
Restart=on-failure
RestartSec=5
RestartForceExitStatus=75
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$MOBAIDE_AGENT_PREFIX
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
    chown -R "$MOBAIDE_AGENT_SERVICE_USER:$MOBAIDE_AGENT_SERVICE_USER" "$MOBAIDE_AGENT_PREFIX"
    systemctl daemon-reload
    systemctl enable mobaide-agent
    systemctl restart mobaide-agent
}

install_launchd_plist() {
    local plist="$HOME/Library/LaunchAgents/com.mobaide.agent.plist"
    info "Installing launchd plist at $plist"
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mobaide.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MOBAIDE_AGENT_PREFIX</key><string>$MOBAIDE_AGENT_PREFIX</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$MOBAIDE_AGENT_PREFIX/agent.out.log</string>
  <key>StandardErrorPath</key><string>$MOBAIDE_AGENT_PREFIX/agent.err.log</string>
</dict>
</plist>
EOF
    mkdir -p "$MOBAIDE_AGENT_PREFIX/bin"
    cat > "$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -e
prefix="${MOBAIDE_AGENT_PREFIX:-/usr/local/mobaide}"
version="$(cat "$prefix/current_version")"
exec "$prefix/releases/$version/venv/bin/python" -m mobaide.launcher
LAUNCHER
    chmod +x "$MOBAIDE_AGENT_PREFIX/bin/mobaide-agent-launcher.sh"

    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
}

print_completion() {
    echo
    echo "============================================"
    echo "  Mobaide agent installed"
    echo "  Prefix:  $MOBAIDE_AGENT_PREFIX"
    echo "  Version: $(cat "$MOBAIDE_AGENT_PREFIX/current_version")"
    if [ "$PLATFORM" = "linux" ]; then
        echo "  Status:  systemctl status mobaide-agent"
        echo "  Logs:    journalctl -u mobaide-agent -f"
    else
        echo "  Logs:    tail -f $MOBAIDE_AGENT_PREFIX/agent.err.log"
    fi
    echo "============================================"
}

main() {
    detect_platform
    require_root_when_writing_system_dir
    info "Installing mobaide_agent into $MOBAIDE_AGENT_PREFIX"
    check_command curl
    check_command tar
    check_python

    mkdir -p "$MOBAIDE_AGENT_PREFIX"
    write_trust_key

    local manifest
    manifest="$(mktemp)"
    fetch_manifest "$manifest"
    verify_manifest_signature "$manifest"
    stage_release "$manifest"
    rm -f "$manifest"

    if [ "$PLATFORM" = "linux" ]; then
        ensure_service_user
        install_systemd_unit
    else
        install_launchd_plist
    fi

    print_completion
}

main "$@"
