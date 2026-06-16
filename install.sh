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
: "${MOBAIDE_AGENT_MANIFEST_URL:=https://api.mobaide.com/api/v1/agent/manifest.json}"
: "${MOBAIDE_AGENT_TRUST_KEY_B64:=}"
# Self-host / loopback escape hatch for the trust-key fetch. Defaults to "0"
# (HTTPS-only); set to "1" only on a trusted local network.
: "${MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH:=0}"
# The agent runs as a real user (not a system account) so it can read/write
# their own /home/<user>/<repo> source trees. With `sudo curl|bash` this
# resolves from $SUDO_USER; override explicitly with MOBAIDE_RUN_AS_USER for
# CI / multi-user hosts where SUDO_USER is unset or wrong.
: "${MOBAIDE_RUN_AS_USER:=}"
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
            --preserve-env=MOBAIDE_AGENT_PREFIX,MOBAIDE_AGENT_MANIFEST_URL,MOBAIDE_AGENT_TRUST_KEY_B64,MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH,MOBAIDE_RUN_AS_USER,MOBAIDE_AGENT_INSTALL_URL \
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
    # Intentionally NOT `local`: $version is consumed downstream by
    # ensure_ensurepip_available to pick the right `python3.X-venv` apt
    # package on Debian/Ubuntu. Same script-scope idiom this file already
    # uses for $tmpdir in stage_release().
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major minor
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 12 ]; }; then
        fail "Python 3.12+ is required (found $version)."
    fi
    info "Python $version detected."
}

ensure_ensurepip_available() {
    if [ "$PLATFORM" != "linux" ]; then return 0; fi
    if python3 -c 'import ensurepip' >/dev/null 2>&1; then return 0; fi

    info "python3 'ensurepip' module missing — required by 'python3 -m venv'."
    info "Common on Debian/Ubuntu when the python3-venv package is not installed."

    if [ "$(id -u)" -ne 0 ]; then
        fail "Cannot auto-install the venv prerequisite without root.
You are installing into MOBAIDE_AGENT_PREFIX=$MOBAIDE_AGENT_PREFIX (user-writable),
so the script did not auto-elevate. Install the package manually and re-run:
    Debian/Ubuntu: sudo apt-get install -y python${version}-venv  (or python3-venv)
    Fedora/RHEL:   sudo dnf install -y python3-virtualenv
    Arch:          sudo pacman -S --noconfirm python
    openSUSE:      sudo zypper install -y python3-virtualenv
    Alpine:        sudo apk add py3-virtualenv"
    fi

    local installed=0
    if command -v apt-get >/dev/null 2>&1; then
        info "Installing python${version}-venv via apt-get (non-interactive)."
        export DEBIAN_FRONTEND=noninteractive
        if apt-get install -y --no-install-recommends "python${version}-venv" </dev/null >/dev/null 2>&1 \
           || apt-get install -y --no-install-recommends python3-venv </dev/null >/dev/null 2>&1; then
            installed=1
        else
            info "Initial install failed — refreshing apt cache and retrying."
            if apt-get update -qq </dev/null >/dev/null 2>&1 \
               && { apt-get install -y --no-install-recommends "python${version}-venv" </dev/null >/dev/null 2>&1 \
                    || apt-get install -y --no-install-recommends python3-venv </dev/null >/dev/null 2>&1; }; then
                installed=1
            fi
        fi
    elif command -v dnf >/dev/null 2>&1; then
        info "Installing python3-virtualenv via dnf."
        dnf install -y python3-virtualenv </dev/null >/dev/null 2>&1 && installed=1
    elif command -v yum >/dev/null 2>&1; then
        info "Installing python3-virtualenv via yum."
        yum install -y python3-virtualenv </dev/null >/dev/null 2>&1 && installed=1
    elif command -v pacman >/dev/null 2>&1; then
        info "Refreshing pacman db and ensuring python (includes venv)."
        pacman -Sy --noconfirm --needed python </dev/null >/dev/null 2>&1 && installed=1
    elif command -v zypper >/dev/null 2>&1; then
        info "Installing python3-virtualenv via zypper."
        zypper --non-interactive install python3-virtualenv </dev/null >/dev/null 2>&1 && installed=1
    elif command -v apk >/dev/null 2>&1; then
        info "Installing py3-virtualenv via apk."
        apk add --no-cache py3-virtualenv </dev/null >/dev/null 2>&1 && installed=1
    else
        fail "No supported package manager found (tried apt-get, dnf, yum, pacman, zypper, apk).
Install the Python venv/ensurepip prerequisite for your distro and re-run."
    fi

    [ "$installed" -eq 1 ] || fail "Package manager invocation failed for the venv prerequisite.
Try manually: install python${version}-venv (Debian/Ubuntu) or your distro's equivalent."

    if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
        fail "Installed the venv package but 'import ensurepip' still fails.
Open an issue with the output of: python3 -c 'import ensurepip'; uname -a; cat /etc/os-release"
    fi
    info "ensurepip available."
}

# Trust-key validation (``_validate_trust_key_or_fail``) and manifest-signature
# verification (``verify_manifest_signature``) import ``cryptography`` — a
# PyPI dep that is NOT in stdlib. On Debian/Ubuntu the system tools that
# depend on it (cloud-init, snapd) drag it in, but on macOS, Alpine, fresh
# Fedora minimal, and the python.org installer it is absent. Those checks
# run BEFORE the wheel is fetched (which carries cryptography transitively),
# so the install would hard-fail with ``ModuleNotFoundError`` on every host
# without a pre-existing cryptography install.
#
# Sets two script-scoped variables (intentionally NOT ``local`` — the
# centralized ``cleanup_on_exit`` trap installed by ``main()`` resolves
# ``$BOOTSTRAP_DIR`` from this scope, and ``local`` would hide it):
#   * ``VERIFIER_PYTHON`` — interpreter that can ``import cryptography``.
#     Either ``python3`` (fast path: host already has it) or the bootstrap
#     venv's python (slow path: ephemeral install).
#   * ``BOOTSTRAP_DIR`` — bootstrap venv root if created, empty string
#     otherwise. The cleanup trap deletes it on EXIT (success or failure).
resolve_verifier_python() {
    if python3 -c 'import cryptography' >/dev/null 2>&1; then
        info "cryptography already importable on python3; skipping bootstrap venv."
        VERIFIER_PYTHON="python3"
        BOOTSTRAP_DIR=""
        return 0
    fi
    BOOTSTRAP_DIR="$(mktemp -d)"
    info "Bootstrapping verification venv (cryptography>=43.0)..."
    if ! python3 -m venv "$BOOTSTRAP_DIR"; then
        fail "Could not create a verification venv (python3 -m venv failed).
The base interpreter is missing or has a broken venv/ensurepip. Ensure
python${version}-venv (or the equivalent for your distro) is installed and
re-run."
    fi
    # ``--no-cache-dir`` matches stage_release's pip flags (line ~389) — this
    # is an ephemeral venv, the cache would be deleted with it.
    # ``--disable-pip-version-check`` + ``--quiet`` suppress noise on a
    # throwaway one-shot install. Errors still surface (pip writes them to
    # stderr and exits non-zero).
    #
    # Lock down pip's runtime configuration so a hostile-configured host
    # cannot redirect ``cryptography`` to an attacker-controlled mirror.
    # A compromised cryptography wheel could forge a passing manifest
    # signature verify — the very check this venv exists for — so the
    # bootstrap install must be its own root of trust on top of TLS.
    #
    # Two layers, both required:
    #   * ``--index-url https://pypi.org/simple`` — declares the canonical
    #     primary index explicitly.
    #   * ``--isolated`` — tells pip to ignore environment variables
    #     (``PIP_EXTRA_INDEX_URL``, ``PIP_FIND_LINKS``, ``PIP_TRUSTED_HOST``,
    #     ``PIP_INDEX_URL``, ...) AND user/system config files
    #     (``~/.pip/pip.conf``, ``/etc/pip.conf``). Without ``--isolated``,
    #     ``--index-url`` alone leaves ``PIP_EXTRA_INDEX_URL`` and
    #     ``PIP_FIND_LINKS`` in play — pip treats those as ADDITIVE sources
    #     with no priority, so an attacker publishes
    #     ``cryptography==999.0.0`` on their mirror and pip prefers it by
    #     version (classic dependency-confusion vector,
    #     CVE-2018-20225-class).
    #
    # The agent's release wheel goes through the manifest's ``wheel_url``
    # rather than PyPI, so this lockdown matches the trust posture of the
    # rest of the install. Operators behind an air-gapped/self-hosted PyPI
    # mirror can pre-install ``cryptography>=43.0`` on the system Python
    # (fast path), bypassing this slow-path entirely.
    if ! "$BOOTSTRAP_DIR/bin/pip" install --isolated --no-cache-dir --disable-pip-version-check --quiet --index-url https://pypi.org/simple "cryptography>=43.0"; then
        fail "Could not install the cryptography package required to verify
the manifest signature. Most likely causes:
  * No network access to PyPI (pypi.org).
  * A corporate proxy blocking pypi.org — set HTTPS_PROXY / HTTP_PROXY.
  * No prebuilt wheel for this Python/arch (check
    https://pypi.org/project/cryptography for the matching wheel).
Workaround: pre-install on the system Python (pip install 'cryptography>=43.0')
and re-run."
    fi
    VERIFIER_PYTHON="$BOOTSTRAP_DIR/bin/python"
}

# Centralized EXIT trap installed by main() — single source of truth for the
# transient temp dirs this installer creates:
#   * $tmpdir          — stage_release's tarball/wheel scratch (mktemp -d
#                        at line ~320). NOT local in stage_release so this
#                        trap can see it from a deferred-expansion context.
#   * $BOOTSTRAP_DIR   — resolve_verifier_python's bootstrap venv root.
# ``${var:-}`` defaults are essential: ``set -u`` would otherwise trip when
# the trap fires before either variable is set (fail-fast paths in
# detect_platform / check_python). Future contributors adding a third
# temp dir must extend this function, not install a second trap that
# would override this one.
cleanup_on_exit() {
    [ -n "${tmpdir:-}" ] && rm -rf -- "$tmpdir"
    [ -n "${BOOTSTRAP_DIR:-}" ] && rm -rf -- "$BOOTSTRAP_DIR"
    return 0
}

fetch_manifest() {
    local manifest_path="$1"
    info "Fetching manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    if ! curl -fsSL --max-time 30 "$MOBAIDE_AGENT_MANIFEST_URL" -o "$manifest_path"; then
        fail "Could not fetch manifest from $MOBAIDE_AGENT_MANIFEST_URL"
    fi
}

# Round-trip validate that ``$MOBAIDE_AGENT_TRUST_KEY_B64`` decodes to a
# 32-byte Ed25519 public key. Used by both the env-var-pinned path and the
# central-server-fetched path so a malformed key surfaces immediately at
# install time as "your key is malformed" rather than later as a confusing
# "manifest signature does not verify".
_validate_trust_key_or_fail() {
    "$VERIFIER_PYTHON" - "$MOBAIDE_AGENT_TRUST_KEY_B64" <<'PY'
import base64, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

key_b64 = sys.argv[1]
try:
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
except Exception as exc:
    sys.exit(f"trust key is not a valid base64 Ed25519 public key: {exc}")
PY
}

# Trust-key bootstrap: ensure ``$MOBAIDE_AGENT_TRUST_KEY_B64`` is set before
# fetching the manifest. If the operator pinned a value, validate and use
# it. Otherwise fetch the manifest-signing public key from the
# central_server endpoint (host derived from
# ``$MOBAIDE_AGENT_MANIFEST_URL``). Fail-closed if neither path yields a
# valid key — never silently ship an install whose auto-updater can't
# verify rollouts.
#
# Two security guards on the fetch path:
# * User-info in ``$MOBAIDE_AGENT_MANIFEST_URL`` (e.g.
#   ``https://api.mobaide.com@evil.example/...``) is rejected — would
#   otherwise DNS-resolve to ``evil.example`` and pin an attacker key.
# * ``http://`` schemes refused unless
#   ``MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1`` is set — a passive
#   on-path attacker could substitute the public key over cleartext.
bootstrap_trust_key() {
    if [ -n "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        info "Using operator-pinned trust key (MOBAIDE_AGENT_TRUST_KEY_B64)."
        _validate_trust_key_or_fail
        return 0
    fi

    # Parse $MOBAIDE_AGENT_MANIFEST_URL into a clean scheme://host[:port]
    # base. The Python heredoc rejects user-info up front (urlparse's
    # ``netloc`` would otherwise smuggle ``user@host`` into the trust-key
    # URL and DNS-resolve to the wrong host) and rebuilds the origin from
    # ``hostname`` + optional ``port`` so the netloc surface is fully
    # under our control.
    local origin
    origin="$(printf '%s\n' "$MOBAIDE_AGENT_MANIFEST_URL" | python3 -c '
import os, sys, urllib.parse as p

raw = sys.stdin.read().strip()
u = p.urlparse(raw)
if not u.scheme or not u.hostname:
    sys.exit(f"MOBAIDE_AGENT_MANIFEST_URL is not a valid URL: {raw!r}")
if u.username is not None or u.password is not None:
    sys.exit(
        f"MOBAIDE_AGENT_MANIFEST_URL must not contain user-info "
        f"(user@/user:pass@): {raw!r}"
    )
scheme = u.scheme.lower()
if scheme not in ("http", "https"):
    sys.exit(
        f"MOBAIDE_AGENT_MANIFEST_URL has unsupported scheme {scheme!r}: {raw!r}"
    )
if scheme == "http" and os.environ.get(
    "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH"
) != "1":
    sys.exit(
        f"Trust-key fetch over cleartext http:// is refused by default "
        f"(MOBAIDE_AGENT_MANIFEST_URL={raw!r}). Set "
        f"MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1 to override "
        f"(only safe on a trusted local network, e.g. self-host loopback)."
    )
host = u.hostname
if ":" in host and not host.startswith("["):
    host = f"[{host}]"
if u.port is not None:
    host = f"{host}:{u.port}"
print(f"{scheme}://{host}")
')"
    if [ -z "$origin" ]; then
        fail "Could not derive a trust-key endpoint from MOBAIDE_AGENT_MANIFEST_URL=$MOBAIDE_AGENT_MANIFEST_URL"
    fi

    local trust_url="${origin}/api/v1/agent/trust_key"
    info "Fetching trust key from $trust_url"
    local body
    if ! body="$(curl -fsSL --max-time 30 "$trust_url")"; then
        fail "Could not bootstrap trust key from $trust_url.
Set MOBAIDE_AGENT_TRUST_KEY_B64=<base64-ed25519> explicitly,
or ensure central_server has MANIFEST_SIGNING_PUBLIC_KEY configured.
See docs/versioning-and-updates.md → Trust-key bootstrap."
    fi
    MOBAIDE_AGENT_TRUST_KEY_B64="$(printf '%s' "$body" | python3 -c 'import json, sys; print(json.load(sys.stdin)["public_key_b64"].strip())')"
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        fail "Trust-key endpoint returned an empty public_key_b64."
    fi
    # Round-trip validate fail-closed BEFORE any files land on disk so a
    # misconfigured central_server can't smuggle in a malformed key.
    _validate_trust_key_or_fail
    info "Trust key bootstrapped from central_server."
}

verify_manifest_signature() {
    local manifest_path="$1"
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        # ``bootstrap_trust_key`` should have populated this — defensive check
        # only fires if a future refactor reorders the call sites.
        fail "Internal error: trust key not bootstrapped before signature verification."
    fi
    info "Verifying manifest signature"
    "$VERIFIER_PYTHON" - "$manifest_path" "$MOBAIDE_AGENT_TRUST_KEY_B64" <<'PY'
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
    # Intentionally NOT `local`: the centralized `cleanup_on_exit` trap
    # installed at the top of `main()` resolves `$tmpdir` from this scope
    # via deferred expansion (`[ -n "${tmpdir:-}" ] && rm -rf -- "$tmpdir"`).
    # With `local`, the variable would go out of scope before the trap
    # fires and `set -u` would trip on the guard. Keeping `tmpdir`
    # script-scoped also lets a half-finished stage_release (any path
    # that exits non-zero under `set -e` between mktemp and the explicit
    # cleanup below) still surrender the directory on EXIT.
    tmpdir="$(mktemp -d)"

    # Capture the heredoc's stdout: the Python script prints exactly one line
    # — the staged wheel's full path. pip 25.2+ enforces PEP 427 wheel-filename
    # validation, so the wheel must be staged under its canonical
    # name-version-pyver-abi-platform filename, derived from the manifest's
    # `wheel_url` rather than a static placeholder.
    local wheel_path
    wheel_path="$(python3 - "$manifest_path" "$tmpdir" "$release_dir" <<'PY'
import hashlib, json, posixpath, shutil, sys, tarfile, urllib.parse, urllib.request
from pathlib import Path

manifest_path, tmpdir, release_dir = sys.argv[1:4]
record = json.loads(Path(manifest_path).read_text())
body = record["body"]

# Identify ourselves so a Cloudflare-fronted wheel host doesn't classify the
# request as the default ``Python-urllib/X.Y`` UA (Bot Fight Mode 1010).
USER_AGENT = f"mobaide-agent-installer/{body.get('version', 'unknown')}"

def fetch(url, dest):
    if url.startswith("file://") or url.startswith("/"):
        shutil.copyfile(url.removeprefix("file://"), dest)
        return
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)

def verify_sha256(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        sys.exit(f"sha256 mismatch for {path}")

def wheel_basename(url):
    if url.startswith("file://"):
        name = Path(url.removeprefix("file://")).name
    else:
        name = posixpath.basename(urllib.parse.urlparse(url).path)
    if any(ch in name for ch in ("/", "\\", "\x00")):
        sys.exit(
            f"manifest wheel_url basename contains path-separator chars: {name!r} (from {url!r})"
        )
    if not name.endswith(".whl") or name.count("-") < 4:
        sys.exit(
            f"manifest wheel_url has non-PEP427 filename: {name!r} (from {url!r})"
        )
    return name

tarball = Path(tmpdir) / "agent.tar.gz"
wheel = Path(tmpdir) / wheel_basename(body["wheel_url"])
fetch(body["tarball_url"], tarball)
verify_sha256(tarball, body["tarball_sha256"])
fetch(body["wheel_url"], wheel)
verify_sha256(wheel, body["wheel_sha256"])

with tarfile.open(tarball, mode="r:gz") as tar:
    tar.extractall(release_dir, filter="data")

print(wheel)
PY
)"

    info "Creating venv in $release_dir/venv"
    python3 -m venv "$release_dir/venv"
    "$release_dir/venv/bin/pip" install --no-cache-dir --upgrade pip
    "$release_dir/venv/bin/pip" install --no-cache-dir "$wheel_path"

    rm -rf "$tmpdir"

    printf '%s\n' "$target_version" > "${MOBAIDE_AGENT_PREFIX}/current_version.tmp"
    mv "${MOBAIDE_AGENT_PREFIX}/current_version.tmp" "${MOBAIDE_AGENT_PREFIX}/current_version"
    info "Pinned current_version=$target_version"
}

write_trust_key() {
    # Always called after ``bootstrap_trust_key`` has populated
    # ``$MOBAIDE_AGENT_TRUST_KEY_B64``, so the file is always written.
    # The agent's auto-updater requires this file at runtime
    # (mobaide.agent.install_paths.read_trust_public_key).
    if [ -z "$MOBAIDE_AGENT_TRUST_KEY_B64" ]; then
        fail "Internal error: write_trust_key called without a bootstrapped key."
    fi
    mkdir -p "${MOBAIDE_AGENT_PREFIX}/trust"
    printf '%s\n' "$MOBAIDE_AGENT_TRUST_KEY_B64" > "${MOBAIDE_AGENT_PREFIX}/trust/central_server.pub"
}

write_agent_env() {
    # Generate <prefix>/mobaide_agent/.env on first install (idempotent).
    # No --force on installer-driven invocations: a paired agent's
    # RELAY_AGENT_ID/TOKEN are never clobbered by re-running the installer.
    local version
    version="$(cat "${MOBAIDE_AGENT_PREFIX}/current_version")"
    local release_dir="${MOBAIDE_AGENT_PREFIX}/releases/${version}"
    info "Writing prepared agent .env via init-env"
    "$release_dir/venv/bin/python" -m mobaide.agent.config init-env \
        --prefix "$MOBAIDE_AGENT_PREFIX"
}

# Parse a KEY=VALUE line from ``<prefix>/mobaide_agent/.env``.
# Mirrors the PowerShell helper in ``deploy/install.ps1``; used by
# ``wait_agent_healthy`` to discover the agent's HTTP port without
# importing Python. Naive split on the first ``=`` matches what
# pydantic-settings + the env-writer agree on at boot (one key per
# line, no quoting; see mobaide.agent.config.init-env).
read_env_value() {
    local path="$1" key="$2" default="${3:-}"
    if [ ! -f "$path" ]; then
        printf '%s' "$default"
        return 0
    fi
    local line value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "$key"=*|" $key"=*|"	$key"=*)
                value="${line#*=}"
                printf '%s' "$value"
                return 0
                ;;
        esac
    done < "$path"
    printf '%s' "$default"
}

# Poll the agent's /api/health endpoint until it responds 200, or
# hard-fail with a journalctl / log-tail pointer. Same role as
# ``Wait-AgentHealthy`` in install.ps1: distinguishes "supervisor
# reported started, service silently crashed" from "service is
# healthy". On Linux, systemd starts quickly enough that this almost
# always succeeds on the first poll; the guard mostly catches
# crashes-at-boot that would previously have hidden behind the
# "rendered QR doesn't pair" symptom.
wait_agent_healthy() {
    local timeout_sec="${1:-60}"
    local env_file="${MOBAIDE_AGENT_PREFIX}/mobaide_agent/.env"
    local port
    port="$(read_env_value "$env_file" 'PORT' '8000')"
    local url="http://127.0.0.1:${port}/api/health"
    # main() captures this function's stdout into $health_json, so any
    # diagnostic must go to stderr or it would corrupt the JSON body
    # downstream. ``info`` writes to stdout by default; force stderr.
    info "Waiting for agent /api/health at ${url} (timeout ${timeout_sec}s)" >&2
    local deadline=$(( $(date +%s) + timeout_sec ))
    local body
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if body="$(curl -fsS --max-time 2 "$url" 2>/dev/null)"; then
            printf '%s' "$body"
            return 0
        fi
        sleep 0.5
    done
    if [ "$PLATFORM" = "linux" ]; then
        fail "Agent service registered but ${url} did not respond within ${timeout_sec}s.
Service may have crashed at startup. Inspect:
    systemctl status mobaide-agent
    journalctl -u mobaide-agent -n 200"
    else
        fail "Agent service registered but ${url} did not respond within ${timeout_sec}s.
Service may have crashed at startup. Inspect:
    launchctl list | grep com.mobaide.agent
    tail -n 200 ${MOBAIDE_AGENT_PREFIX}/agent.err.log"
    fi
}

show_pairing_qr_from_service() {
    # Render the pairing QR after the agent service is up. The running
    # service's boot-time pairing-monitor calls /api/pairing/initiate
    # and writes <prefix>/run/pending_pairing.json; this function waits
    # for the file and renders it via the CLI. Mirrors
    # ``Show-PairingQrFromService`` in install.ps1.
    local health_json="$1"
    local pending_timeout_sec="${2:-60}"
    if [ -n "${MOBAIDE_AGENT_SKIP_INSTALL_QR:-}" ]; then
        info "MOBAIDE_AGENT_SKIP_INSTALL_QR set; not rendering pairing QR."
        return 0
    fi
    # Re-install on a paired agent: pairing_pending=false → skip QR.
    # Use Python for the JSON read to stay portable across distros
    # (jq is not guaranteed; bash's no-JSON is a fact of life).
    local pairing_pending
    pairing_pending="$(printf '%s' "$health_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("true")
else:
    print("true" if data.get("pairing_pending") else "false")
')"
    if [ "$pairing_pending" = "false" ]; then
        info "Agent reports pairing_pending=false; already paired. Skipping QR."
        return 0
    fi
    local version release_dir pending_file
    version="$(cat "${MOBAIDE_AGENT_PREFIX}/current_version")"
    release_dir="${MOBAIDE_AGENT_PREFIX}/releases/${version}"
    pending_file="${MOBAIDE_AGENT_PREFIX}/run/pending_pairing.json"
    info "Waiting for pairing nonce at ${pending_file} (timeout ${pending_timeout_sec}s)"
    local deadline=$(( $(date +%s) + pending_timeout_sec ))
    while [ "$(date +%s)" -lt "$deadline" ] && [ ! -f "$pending_file" ]; do
        sleep 0.5
    done
    if [ ! -f "$pending_file" ]; then
        fail "Agent service is up but did not initiate pairing within ${pending_timeout_sec}s
(no ${pending_file}). The service likely cannot reach central_server. Check:
    curl -fsS ${MOBAIDE_AGENT_MANIFEST_URL%/api/*}/api/v1/agent/trust_key
The service is still running; once connectivity is restored it will
initiate a nonce on its own. Run 'mobaide-agent show-qr' to re-display."
    fi
    info "Rendering pairing QR (scan from the Mobaide mobile app)"
    # --require-pending guards against a double-initiate race with the
    # service's pairing-monitor: the CLI renders the existing file or
    # exits 7, never initiates a second nonce. Two concurrent
    # /ws/pairing/{nonce} dials for the same nonce would race; the
    # central server rejects the loser with WS close 4001.
    "$release_dir/venv/bin/mobaide-agent" show-qr --require-pending \
        --prefix "$MOBAIDE_AGENT_PREFIX"
}

resolve_run_as_user() {
    # Linux only — macOS installs to ~/ and runs under launchd as the user.
    # Sets script-scoped RUN_USER, RUN_GROUP, RUN_HOME consumed by
    # install_systemd_unit. Not `local`: those are read from a different
    # function frame.
    if [ "$PLATFORM" != "linux" ]; then return 0; fi

    if [ -n "$MOBAIDE_RUN_AS_USER" ]; then
        RUN_USER="$MOBAIDE_RUN_AS_USER"
    elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        RUN_USER="$SUDO_USER"
    else
        fail "Could not determine which user to run the agent as.
The agent manages developer code under /home/<user> and must run as a real
user — not 'root' and not a system account.

Re-run with sudo (so SUDO_USER is set):
    sudo curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | bash

Or pin the user explicitly (e.g. for CI / non-interactive provisioning):
    MOBAIDE_RUN_AS_USER=alice curl -fsSL $MOBAIDE_AGENT_INSTALL_URL | sudo -E bash"
    fi

    if ! id "$RUN_USER" >/dev/null 2>&1; then
        fail "MOBAIDE_RUN_AS_USER='$RUN_USER' does not exist on this host."
    fi
    if [ "$RUN_USER" = "root" ]; then
        fail "Refusing to run the agent as 'root'. Set MOBAIDE_RUN_AS_USER to a regular user."
    fi

    RUN_GROUP="$(id -gn "$RUN_USER")"
    RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
    if [ -z "$RUN_HOME" ]; then
        fail "Could not resolve home directory for user '$RUN_USER'."
    fi
    if [ "$RUN_HOME" = "/" ]; then
        # Renders as `ReadWritePaths=/ <prefix>`, which neutralises
        # ProtectSystem=strict by re-granting writes to the entire root
        # filesystem. Refuse rather than silently weakening the unit.
        fail "Resolved HOME for '$RUN_USER' is '/'; refusing — agent would have writes over the entire root filesystem.
Pick a regular user account: MOBAIDE_RUN_AS_USER=<user>."
    fi

    info "Agent will run as $RUN_USER ($RUN_GROUP), HOME=$RUN_HOME"

    # Best-effort cleanup of the legacy 'mobaide' system user from older
    # installs. Skip if it doesn't exist or is in use; never fail the install.
    if id mobaide >/dev/null 2>&1 && [ "$RUN_USER" != "mobaide" ]; then
        info "Removing legacy 'mobaide' system user from prior installs"
        userdel mobaide >/dev/null 2>&1 || warn "Could not remove legacy 'mobaide' user (still in use?). Continuing."
    fi
}

install_systemd_unit() {
    local unit="/etc/systemd/system/mobaide-agent.service"
    info "Installing systemd unit at $unit (User=$RUN_USER)"
    # ProtectHome is absent because the agent must read/write the user's
    # source tree under $RUN_HOME. ProtectSystem=strict still mounts the
    # rest of the OS hierarchy read-only; ReadWritePaths re-grants writes
    # to exactly what the agent needs ($RUN_HOME for code, the install
    # prefix for releases / agent.env / projects.json). PrivateTmp +
    # NoNewPrivileges complete the residual hardening.
    # StartLimitBurst/IntervalSec give the launcher's 3-strike rollback
    # heuristic room to retry a stable sibling release before systemd
    # gives up; RestartForceExitStatus=75 lets the updater request its
    # own restart.
    cat > "$unit" <<EOF
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
WorkingDirectory=$MOBAIDE_AGENT_PREFIX
EnvironmentFile=-$MOBAIDE_AGENT_PREFIX/agent.env
EnvironmentFile=-$MOBAIDE_AGENT_PREFIX/mobaide_agent/.env
Environment=MOBAIDE_AGENT_PREFIX=$MOBAIDE_AGENT_PREFIX
Environment=HOME=$RUN_HOME
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/snap/bin:%h/.npm-global/bin:%h/.local/bin
ExecStart=/usr/bin/env bash -c '$MOBAIDE_AGENT_PREFIX/releases/\$(cat $MOBAIDE_AGENT_PREFIX/current_version)/venv/bin/python -m mobaide.launcher'
Restart=on-failure
RestartSec=5
RestartForceExitStatus=75
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$RUN_HOME $MOBAIDE_AGENT_PREFIX
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
    chown -R "$RUN_USER:$RUN_GROUP" "$MOBAIDE_AGENT_PREFIX"
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
    <key>PATH</key><string>/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/opt/homebrew/bin:\$HOME/.npm-global/bin:\$HOME/.local/bin</string>
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
py="$prefix/releases/$version/venv/bin/python"
# python.org framework Python ships an empty OpenSSL CA path; point OpenSSL at
# the venv's certifi bundle so the agent's wss/HTTPS TLS verifies. Resolved per
# launch so it tracks the active release after an auto-update; respects an
# operator-set SSL_CERT_FILE (e.g. a corporate CA).
if [ -z "${SSL_CERT_FILE:-}" ]; then
  cert="$("$py" -m certifi 2>/dev/null || true)"
  if [ -n "$cert" ]; then
    export SSL_CERT_FILE="$cert"
  fi
fi
exec "$py" -m mobaide.launcher
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
    echo
    echo "  Re-render QR:  mobaide-agent show-qr"
    echo "  Re-pair:       mobaide-agent re-pair"
    echo "============================================"
}

main() {
    # Single installer-wide EXIT trap. Cleans up ``$tmpdir``
    # (stage_release's scratch) and ``$BOOTSTRAP_DIR``
    # (resolve_verifier_python's bootstrap venv). stage_release no longer
    # installs its own trap — overriding this one would silently drop the
    # bootstrap-venv cleanup.
    trap cleanup_on_exit EXIT
    detect_platform
    require_root_when_writing_system_dir
    info "Installing mobaide_agent into $MOBAIDE_AGENT_PREFIX"
    check_command curl
    check_command tar
    check_python
    ensure_ensurepip_available
    resolve_verifier_python

    mkdir -p "$MOBAIDE_AGENT_PREFIX"
    # Order: bootstrap key (env var or central_server fetch) → pin to disk
    # so the agent picks it up at runtime → fetch manifest → verify it
    # against the same key → stage. ``bootstrap_trust_key`` fails the
    # install with an actionable message if neither path yields a key.
    bootstrap_trust_key
    write_trust_key

    local manifest
    manifest="$(mktemp)"
    fetch_manifest "$manifest"
    verify_manifest_signature "$manifest"
    stage_release "$manifest"
    rm -f "$manifest"

    write_agent_env

    # New ordering: bring the agent service up FIRST, then render the
    # QR from the file the running service's pairing-monitor writes.
    # The previous order rendered the QR before the service was up,
    # so a mobile scan during the gap had nothing to dispatch the
    # claim to (the pairing WebSocket is dialed inside the agent
    # process; until uvicorn binds, the central server can't push the
    # signed claim). Mirrors the install.ps1 reorder.
    if [ "$PLATFORM" = "linux" ]; then
        resolve_run_as_user
        install_systemd_unit
    else
        install_launchd_plist
    fi

    local health_json
    health_json="$(wait_agent_healthy 60)"
    show_pairing_qr_from_service "$health_json" 60

    print_completion
}

main "$@"
