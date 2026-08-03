#!/usr/bin/env python3
"""deploy_central.py — Ship central_server to mobaide.com from Windows or macOS.

Usage:
    python deploy/prod/deploy_central.py                              # LetsEncrypt TLS, full deploy
    python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin
    python deploy/prod/deploy_central.py --skip-build                 # restart current image, no source re-sync
    python deploy/prod/deploy_central.py --rollback                   # swap central_server <-> central_server.prev on server
    python deploy/prod/deploy_central.py --force                      # allow dirty working tree

What it does:
  1) Preflight: local tools, clean tree, ssh mobaide reachable, /opt/mobaide/.env exists
  2) Tar central_server/ (with excludes) -> scp -> remote atomic swap
  3) scp docker-compose.prod.yml + Caddyfile.<TlsMode> to /opt/mobaide/
  4) ssh: docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
  5) Poll https://mobaide.com/api/health for up to 120s
  6) On compose-up or health failure: auto-rollback by swapping central_server.prev back in

Server-side artifacts (survive across runs):
    /opt/mobaide/central_server/         current source
    /opt/mobaide/central_server.prev/    previous source (rollback target)
    /opt/mobaide/.env                    secrets (NEVER synced)
    /opt/mobaide/.last_tag               short git sha of current deploy
    /opt/mobaide/www/                    SPA releases (owned by deploy_web.py)
    docker volumes: central-server-data, caddy-data, caddy-config

The browser SPA at app.mobaide.com ships separately via deploy_web.py — a
web deploy swaps a symlink and never touches containers.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

MIN_PYTHON = (3, 12)

SSH_HOST = "mobaide"
REMOTE_BASE = "/opt/mobaide"
DOMAIN = "mobaide.com"
HEALTH_URL = f"https://{DOMAIN}/api/health"

DEPLOY_DIR = Path(__file__).resolve().parent  # .../deploy/prod/
PROJECT_ROOT = DEPLOY_DIR.parents[1]
SOURCE_DIR = PROJECT_ROOT / "central_server"
COMPOSE_FILE = DEPLOY_DIR / "docker-compose.prod.yml"

TAR_EXCLUDES = (
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
    "--exclude=.ruff_cache",
    "--exclude=.mypy_cache",
    "--exclude=*.pyc",
    "--exclude=.env",
    "--exclude=.env.*",
    "--exclude=*.db",
    "--exclude=*.db-journal",
    "--exclude=*.db-wal",
    "--exclude=*.db-shm",
    "--exclude=.venv",
    "--exclude=venv",
)


class DeployError(Exception):
    """Deploy failure. An empty message means the details were already printed."""


class CertVerificationError(DeployError):
    """Local TLS trust failure while polling the health URL.

    Says nothing about server health (the workstation could not verify the
    certificate), so callers must never auto-rollback on it — and a source
    rollback could not fix a certificate problem anyway.
    """


class SshResult(NamedTuple):
    exit_code: int
    output: str


# ------------------------------------------------------------ console helpers
CYAN, GREEN, YELLOW, RED, GRAY, RESET = (
    "\x1b[36m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[90m", "\x1b[0m",
)

_ANSI = False


def _enable_ansi() -> bool:
    if "NO_COLOR" in os.environ or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Legacy conhost needs ENABLE_VIRTUAL_TERMINAL_PROCESSING or it prints
        # raw escape bytes; fall back to plain text if it can't be enabled.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return False
        except Exception:
            return False
    return True


def _emit(text: str, color: str) -> None:
    if _ANSI:
        print(f"{color}{text}{RESET}", flush=True)
    else:
        print(text, flush=True)


def step(msg: str) -> None:
    _emit(f"\n==> {msg}", CYAN)


def ok(msg: str) -> None:
    _emit(f"    [OK] {msg}", GREEN)


def warn(msg: str) -> None:
    _emit(f"    [!!] {msg}", YELLOW)


def fail(msg: str) -> None:
    _emit(f"    [XX] {msg}", RED)


def dim(msg: str) -> None:
    _emit(f"    {msg}", GRAY)


def info(msg: str) -> None:
    _emit(msg, CYAN)


def wait_http(url: str, timeout_sec: int = 90, any_status: bool = False) -> None:
    # TLS verification stays on (default context, OS trust store). No PS-5.1
    # protocol shim needed: Python's ssl negotiates TLS 1.2+ natively.
    # any_status=True succeeds on ANY HTTP response (404 included) — used by
    # deploy_web.py's preflight, where "the site block answered at all" is
    # the question and only a transport/TLS failure means not-deployed.
    deadline = time.monotonic() + timeout_sec
    cert_error: ssl.SSLCertVerificationError | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if any_status or resp.status == 200:
                    return
        except urllib.error.HTTPError:
            if any_status:
                return
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                cert_error = e.reason
        except ssl.SSLCertVerificationError as e:
            cert_error = e
        except (OSError, TimeoutError):
            pass
        time.sleep(2)
    if cert_error is not None:
        warn(
            "TLS certificate verification failed. On macOS python.org builds, "
            "run 'Install Certificates.command' or set SSL_CERT_FILE."
        )
        raise CertVerificationError(
            f"could not verify the TLS certificate for {url} within {timeout_sec}s: {cert_error}"
        )
    raise DeployError(f"Timed out after {timeout_sec}s waiting for {url}")


def encode_ssh_payload(script: str) -> str:
    """Base64-encode a bash script for transport inside a single ssh argument.

    Ship the script as base64 inside a single-word ssh argument, then decode
    and pipe into bash on the server. Two Windows-specific problems vanish:
      (1) CreateProcess wraps multi-word args in "..." without escaping
          embedded " chars, so `echo "USER=$u"` reaches bash with a broken
          quoting state -> `unexpected EOF while looking for matching "`.
      (2) Piping the script to `bash -s` from a Windows shell can leave \\r at
          EOL and break args like `head -5` (becomes `head -5\\r`, "invalid
          trailing option").
    Base64 is printable ASCII with no shell metachars, no " and no newlines,
    so it survives both stages untouched.
    """
    normalized = script.replace("\r\n", "\n")
    return base64.b64encode(normalized.encode("utf-8")).decode("ascii")


def invoke_ssh(script: str) -> SshResult:
    b64 = encode_ssh_payload(script)
    proc = subprocess.run(
        ["ssh", SSH_HOST, f"echo {b64} | base64 -d | bash"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return SshResult(proc.returncode, proc.stdout or "")


def invoke_ssh_or_fail(script: str, fail_msg: str) -> str:
    r = invoke_ssh(script)
    if r.exit_code != 0:
        fail(f"{fail_msg} (exit {r.exit_code})")
        for line in r.output.splitlines():
            dim(line)
        if re.search(r"Connection timed out|Connection refused|port 22", r.output, re.IGNORECASE):
            dim(f"  Likely UFW rate-limit on {SSH_HOST} (server drops after 6 SSH connections in 30s).")
            dim("  Wait 30s and retry. Persistent? Check ufw / fail2ban on the server.")
        raise DeployError("")
    return r.output


# ------------------------------------------------------------ remote scripts
# No ssh multiplexing: Git's MSYS2 ssh and Windows OpenSSH disagree on
# ControlPath syntax (Unix socket file vs `\\.\pipe\...`), and which one wins
# depends on PATH order. Rather than picking sides, run each call with
# defaults and keep the total connection count low by batching server-side —
# the server has `ufw limit 22/tcp`, which silently drops SYNs past 6
# connections in 30s, so every saved round-trip matters.

# Batched preflight: one SSH round-trip exercises the transport, reports the
# remote user, and flags every failing prereq at once — so the operator fixes
# them in a single pass instead of discovering them one connection at a time.
# If ssh fails to even reach the remote bash, we surface that as a separate
# reachability error (see the empty-errs branch in remote_preflight).
PREFLIGHT_SCRIPT = f"""set +e
user=$(whoami)
echo "USER=$user"
errs=""
[ -d {REMOTE_BASE} ]          || errs="$errs BASE_MISSING"
[ -w {REMOTE_BASE} ]          || errs="$errs BASE_NOT_WRITABLE"
[ -f {REMOTE_BASE}/.env ]     || errs="$errs ENV_MISSING"
docker info >/dev/null 2>&1 || errs="$errs DOCKER"
if [ -n "$errs" ]; then echo "ERRORS:$errs"; exit 1; fi
echo "OK"
"""

ROLLBACK_SWAP_SCRIPT = f"""set -e
cd {REMOTE_BASE}
rm -rf central_server.swap
if [ -d central_server ]; then mv central_server central_server.swap; fi
mv central_server.prev central_server
if [ -d central_server.swap ]; then mv central_server.swap central_server.prev; else rm -rf central_server.prev; fi
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
"""

AUTO_ROLLBACK_SCRIPT = f"""set -e
cd {REMOTE_BASE}
if [ ! -d central_server.prev ]; then echo no_prev; exit 0; fi
rm -rf central_server.swap
mv central_server central_server.swap
mv central_server.prev central_server
mv central_server.swap central_server.prev
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
"""


def build_extract_script(tag: str) -> str:
    return f"""set -e
cd {REMOTE_BASE}
rm -rf central_server.new
mkdir -p central_server.new tls www/releases
tar -xf central_server.new.tar -C central_server.new --strip-components=1
rm -f central_server.new.tar
if [ -d central_server ]; then
    rm -rf central_server.prev
    mv central_server central_server.prev
fi
mv central_server.new central_server
echo {tag} > .last_tag
"""


def build_deploy_cmd(tls_mode: str, skip_build: bool) -> str:
    up_args = "up -d --remove-orphans" if skip_build else "up -d --build --remove-orphans"
    # One SSH round-trip does: (a) copy Caddyfile.<TlsMode> -> Caddyfile so
    # docker-compose can mount it, (b) compose up (streaming), (c) caddy
    # reload, (d) compose ps.
    # Strict `cp -f`: if Caddyfile.<TlsMode> is missing (e.g. --skip-build on a
    # never-deployed TLS mode), the deploy fails loudly instead of silently
    # running the wrong TLS mode against a stale Caddyfile.
    # The reload step exists because compose never restarts a running caddy
    # container for a bind-mounted file's CONTENT change — without it a
    # Caddyfile-only deploy silently keeps serving the old config. Reload is
    # atomic (a parse failure keeps the old config live and fails this
    # command loudly) and, with stream_close_delay in the app site block,
    # non-disruptive to established WebSockets. When compose just recreated
    # the container the reload is a harmless re-read.
    return (
        f"cd {REMOTE_BASE} && cp -f Caddyfile.{tls_mode} Caddyfile"
        f" && docker compose -f docker-compose.prod.yml {up_args}"
        f" && docker compose -f docker-compose.prod.yml exec -T caddy"
        f" caddy reload --config /etc/caddy/Caddyfile"
        f" && docker compose -f docker-compose.prod.yml ps"
    )


# ------------------------------------------------------------ preflight
def check_local_tools() -> None:
    for t in ("ssh", "scp", "tar", "git"):
        if shutil.which(t) is None:
            raise DeployError(f"{t} not found in PATH")
    ok("Local tools present: ssh, scp, tar, git")


def check_repo_artifacts(caddy_file: Path) -> None:
    if not SOURCE_DIR.exists():
        raise DeployError(f"Source dir missing: {SOURCE_DIR}")
    if not COMPOSE_FILE.exists():
        raise DeployError(f"Compose file missing: {COMPOSE_FILE}")
    if not caddy_file.exists():
        raise DeployError(f"Caddyfile missing: {caddy_file}")
    ok("Repo artifacts present")


def check_clean_tree() -> None:
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # A *failing* git does not block the deploy (parity with the original
    # script) — only a clean git exit with dirty output does.
    if r.returncode == 0 and r.stdout.strip():
        warn("Working tree has uncommitted changes:")
        for line in r.stdout.splitlines():
            dim(f"  {line}")
        raise DeployError("Refusing to deploy dirty tree. Commit first, or re-run with --force.")
    ok("Working tree clean")


def remote_preflight() -> str:
    probe = invoke_ssh(PREFLIGHT_SCRIPT)
    ssh_user = "<unknown>"
    for line in probe.output.splitlines():
        if line.startswith("USER="):
            ssh_user = line[len("USER="):].strip() or "<unknown>"
            break

    if probe.exit_code != 0:
        errs = ""
        for line in probe.output.splitlines():
            if line.startswith("ERRORS:"):
                errs = line[len("ERRORS:"):].strip()
                break
        if not errs:
            # Probe never reached remote bash — SSH transport failure.
            fail(f"Cannot ssh {SSH_HOST}. Raw error:")
            for line in probe.output.splitlines():
                dim(line)
            dim("  Common causes:")
            dim(f"  - Host '{SSH_HOST}' missing or misconfigured in ~/.ssh/config")
            dim("  - Key auth failing (IdentityFile path / agent / server ~/.ssh/authorized_keys)")
            dim("  - UFW rate-limit tripped: server drops after 6 SSH connections in 30s")
            dim("  - Network / sshd unreachable (reboot, firewall)")
            dim("  - 'User root' in ssh config but server has PermitRootLogin no (use the bootstrap user)")
        else:
            if "BASE_MISSING" in errs:
                fail(f"{REMOTE_BASE} does not exist on {SSH_HOST} (run deploy/prod/bootstrap-mobaide.sh)")
            if "BASE_NOT_WRITABLE" in errs:
                fail(
                    f"{REMOTE_BASE} not writable by '{ssh_user}'. Bootstrap chowns it to the user "
                    f"who invoked sudo. Either deploy from that account or run: "
                    f"sudo chown -R {ssh_user}: {REMOTE_BASE}"
                )
            if "ENV_MISSING" in errs:
                fail(f"{REMOTE_BASE}/.env missing on {SSH_HOST} (see bootstrap-mobaide.md)")
            if "DOCKER" in errs:
                fail(
                    f"docker not usable by '{ssh_user}' on {SSH_HOST}. If installed: "
                    f"sudo usermod -aG docker {ssh_user}, then disconnect & reconnect ssh "
                    f"(group membership is per-session)."
                )
        raise DeployError("")

    ok(f"ssh {SSH_HOST} reachable as '{ssh_user}'")
    ok(f"Server prereqs OK ({REMOTE_BASE}, .env, docker, writable by '{ssh_user}')")
    return ssh_user


# ------------------------------------------------------------ rollback
def do_rollback() -> int:
    step("Rollback: swap central_server.prev -> central_server")
    probe = invoke_ssh(f"test -d {REMOTE_BASE}/central_server.prev && echo has_prev || echo no_prev")
    if "has_prev" not in probe.output:
        raise DeployError("No central_server.prev on server; cannot rollback.")
    invoke_ssh_or_fail(ROLLBACK_SWAP_SCRIPT, "Rollback swap/up failed")
    ok("Previous version rebuilt and started")
    wait_http(HEALTH_URL, 120)
    ok(f"Rollback healthy at {HEALTH_URL}")
    return 0


# ------------------------------------------------------------ tag
def resolve_tag() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tag = r.stdout.strip() if r.returncode == 0 else ""
    return tag or datetime.now().strftime("%Y%m%d-%H%M%S")


# ------------------------------------------------------------ sync sources
def sweep_stale_tars() -> None:
    # Sweep stale tarballs (>24h) from past crashed runs without racing a
    # parallel deploy.
    cutoff = time.time() - 24 * 3600
    for p in Path(tempfile.gettempdir()).glob("mobaide-central-*.tar"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def sync_sources(tag: str, caddy_file: Path, tls_mode: str) -> None:
    step("Packaging central_server/ (tar + excludes)")
    sweep_stale_tars()

    tar_local = Path(tempfile.gettempdir()) / f"mobaide-central-{tag}.tar"
    tar_local.unlink(missing_ok=True)

    try:
        # Both Windows System32 tar and macOS tar are bsdtar. If GNU tar (e.g.
        # Git for Windows usr/bin) wins PATH and chokes on the drive-letter
        # colon in the archive path, add --force-local here.
        r = subprocess.run(
            ["tar", "-cf", str(tar_local), *TAR_EXCLUDES, "central_server"],
            cwd=PROJECT_ROOT,
        )
        if r.returncode != 0:
            raise DeployError(f"tar failed (exit {r.returncode})")
        tar_bytes = tar_local.stat().st_size
        ok(f"Archive built: {tar_local} ({tar_bytes:,} bytes)")

        step(f"Uploading tarball to {SSH_HOST} and extracting atomically")
        # scp writes to a file path, not a directory, so it only needs REMOTE_BASE
        # to exist (already verified by preflight). All staging prep (rm old
        # central_server.new/, mkdir central_server.new + tls) is folded into the
        # extract script below to save two SSH round-trips under ufw's 6/30s limit.
        r = subprocess.run(["scp", str(tar_local), f"{SSH_HOST}:{REMOTE_BASE}/central_server.new.tar"])
        if r.returncode != 0:
            raise DeployError("scp tar failed")

        invoke_ssh_or_fail(build_extract_script(tag), "Remote extract/swap failed")
        ok("Source swapped (central_server.prev preserved for rollback)")
    finally:
        tar_local.unlink(missing_ok=True)

    step(f"Uploading docker-compose.prod.yml and Caddyfile ({tls_mode})")
    # Single scp with two sources, trailing slash on dest => both files land in
    # REMOTE_BASE/ with their local names. Caddyfile arrives as Caddyfile.<TlsMode>;
    # the deploy command copies it to `Caddyfile` (the name docker-compose mounts)
    # just before `docker compose up`. tls/ is created by the extract script.
    r = subprocess.run(["scp", str(COMPOSE_FILE), str(caddy_file), f"{SSH_HOST}:{REMOTE_BASE}/"])
    if r.returncode != 0:
        raise DeployError("scp compose+Caddyfile failed")
    ok(f"compose + Caddyfile.{tls_mode} uploaded")


# ------------------------------------------------------------ deploy
def compose_up(tls_mode: str, skip_build: bool) -> int:
    step(f"docker compose up on {SSH_HOST}")
    # Run directly (not base64-wrapped) with inherited stdio to stream build
    # output live; the command is a single line with no quotes to mangle.
    proc = subprocess.run(["ssh", SSH_HOST, build_deploy_cmd(tls_mode, skip_build)])
    return proc.returncode


def attempt_auto_rollback(verify_hint: bool) -> None:
    warn("Attempting auto-rollback")
    roll = invoke_ssh(AUTO_ROLLBACK_SCRIPT)
    if "no_prev" in roll.output:
        fail("No previous version to roll back to.")
    elif roll.exit_code != 0:
        fail(f"Auto-rollback also failed. Investigate manually on {SSH_HOST}.")
    elif verify_hint:
        warn(f"Rolled back. Verify manually: curl {HEALTH_URL}")
    else:
        warn("Rolled back to previous version.")


# ------------------------------------------------------------ summary
def print_summary(tag: str, tls_mode: str) -> None:
    # `docker compose ps` was already streamed at the tail of the deploy
    # command, so the summary section is local-only (no SSH round-trip).
    step("Summary")
    info(f"Deployed tag : {tag}")
    info(f"TLS mode     : {tls_mode}")
    info(f"Health       : {HEALTH_URL}")
    info("Rollback     : python deploy/prod/deploy_central.py --rollback")


def run(args: argparse.Namespace) -> int:
    caddy_file = DEPLOY_DIR / f"Caddyfile.{args.tls_mode}"

    step("Preflight")
    check_local_tools()
    check_repo_artifacts(caddy_file)
    if not args.force and not args.rollback:
        check_clean_tree()
    remote_preflight()

    if args.rollback:
        return do_rollback()

    tag = resolve_tag()
    ok(f"Deploy tag: {tag}")

    if not args.skip_build:
        sync_sources(tag, caddy_file, args.tls_mode)
    else:
        warn("--skip-build: reusing existing source on server")

    compose_exit = compose_up(args.tls_mode, args.skip_build)
    if compose_exit != 0:
        fail(f"docker compose failed (exit {compose_exit})")
        if not args.skip_build:
            attempt_auto_rollback(verify_hint=False)
        return 1
    ok("compose up complete")

    step(f"Health check: {HEALTH_URL} (up to 120s)")
    try:
        wait_http(HEALTH_URL, 120)
        ok("central-server healthy")
    except CertVerificationError as e:
        # Local trust-store problem, not a service failure: the server may be
        # perfectly healthy, so rolling back would only cause churn.
        fail(f"Health check inconclusive: {e}")
        warn(f"NOT rolling back — the server may be healthy. Verify manually: curl {HEALTH_URL}")
        return 1
    except DeployError as e:
        fail(f"Health check failed: {e}")
        if not args.skip_build:
            attempt_auto_rollback(verify_hint=True)
        return 1

    print_summary(tag, args.tls_mode)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy_central.py",
        description="Ship central_server to mobaide.com and rebuild the Docker Compose stack.",
        epilog=(
            "examples:\n"
            "  python deploy/prod/deploy_central.py                              # LetsEncrypt TLS, full deploy\n"
            "  python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin\n"
            "  python deploy/prod/deploy_central.py --skip-build                 # restart current image, no source re-sync\n"
            "  python deploy/prod/deploy_central.py --rollback                   # swap central_server <-> central_server.prev\n"
            "  python deploy/prod/deploy_central.py --force                      # allow dirty working tree\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tls-mode",
        choices=("LetsEncrypt", "CloudflareOrigin"),
        default="LetsEncrypt",
        help="TLS mode; selects deploy/prod/Caddyfile.<TlsMode> (default: %(default)s)",
    )
    parser.add_argument("--skip-build", action="store_true", help="restart current image without re-syncing sources")
    parser.add_argument("--rollback", action="store_true", help="swap central_server.prev back in on the server")
    parser.add_argument("--force", action="store_true", help="allow deploying from a dirty working tree")
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print(
            f"deploy_central.py requires Python {'.'.join(map(str, MIN_PYTHON))}+ "
            f"(found {sys.version.split()[0]})",
            file=sys.stderr,
        )
        return 1
    args = build_parser().parse_args(argv)

    global _ANSI
    _ANSI = _enable_ansi()

    try:
        return run(args)
    except DeployError as e:
        if str(e):
            fail(str(e))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
