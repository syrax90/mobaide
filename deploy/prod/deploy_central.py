#!/usr/bin/env python3
"""deploy_central.py — Ship central_server to mobaide.com from Windows or macOS.

Usage:
    python deploy/prod/deploy_central.py                              # LetsEncrypt TLS, full deploy
    python deploy/prod/deploy_central.py --tls-mode CloudflareOrigin
    python deploy/prod/deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin   # classify a TLS failure, deploy nothing
    python deploy/prod/deploy_central.py --skip-build                 # restart current image, no source re-sync
    python deploy/prod/deploy_central.py --rollback                   # swap central_server <-> central_server.prev on server
    python deploy/prod/deploy_central.py --force                      # allow dirty working tree

What it does:
  1) Preflight: local tools, clean tree, ssh mobaide reachable, /opt/mobaide/.env exists,
     and (CloudflareOrigin mode) the origin cert neither shadows app.mobaide.com nor
     mismatches its key
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

import tls_preflight

MIN_PYTHON = (3, 12)

SSH_HOST = "mobaide"
REMOTE_BASE = "/opt/mobaide"
DOMAIN = "mobaide.com"
HEALTH_URL = f"https://{DOMAIN}/api/health"
APP_URL = f"https://app.{DOMAIN}/"
ORIGIN_CERT = f"{REMOTE_BASE}/tls/origin-cert.pem"
ORIGIN_KEY = f"{REMOTE_BASE}/tls/origin-key.pem"

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


class ClassifiedDeployError(DeployError):
    """A failure `wait_http` already classified and printed.

    Callers must not re-run the classifier on it: the second pass costs
    another probe ladder and can disagree with the first (a flapping host
    yields a hard verdict followed by "transient — re-run the deploy").
    """


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


def enable_ansi() -> None:
    """Arm colored output. Both entry points call this: the emitters and the
    _ANSI flag live here, so deploy_web's color comes from this same global.
    """
    global _ANSI
    _ANSI = _enable_ansi()


def require_min_python(prog: str) -> bool:
    """False (after printing why) when the interpreter is too old. Both entry
    points gate on it — deploy_web runs the same syntax off the same stdlib.
    """
    if sys.version_info >= MIN_PYTHON:
        return True
    print(
        f"{prog} requires Python {'.'.join(map(str, MIN_PYTHON))}+ "
        f"(found {sys.version.split()[0]})",
        file=sys.stderr,
    )
    return False


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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turns 3xx into HTTPError instead of following the Location header."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_ssl_context: ssl.SSLContext | None = None
_ssl_context_ready = False


def http_ssl_context() -> ssl.SSLContext | None:
    """The context every probe uses; None means urllib's default is fine.

    Resolved once so the certifi-fallback warning is printed at most once per
    run, and shared with the control probe — comparing a failing host against
    a known-good one only means something if both use the same trust store.
    """
    global _ssl_context, _ssl_context_ready
    if not _ssl_context_ready:
        _ssl_context, warning = tls_preflight.build_ssl_context()
        if warning:
            warn(warning)
        _ssl_context_ready = True
    return _ssl_context


def report_probe_failure(failure: tls_preflight.ProbeFailure) -> None:
    fail(f"{failure.kind}: {failure.detail}")
    for line in failure.remedy.splitlines():
        dim(f"  {line}")


def report_tls_warning(warning: tls_preflight.TlsWarning) -> None:
    fail(warning.headline)
    for line in warning.lines:
        dim(f"  {line}")


def wait_http(
    url: str,
    timeout_sec: int = 90,
    any_status: bool = False,
    follow_redirects: bool = True,
) -> None:
    # TLS verification stays on (default context, OS trust store). No PS-5.1
    # protocol shim needed: Python's ssl negotiates TLS 1.2+ natively.
    # any_status=True succeeds on ANY HTTP response (404 included) — used by
    # deploy_web.py's preflight, where "the site block answered at all" is
    # the question and only a transport/TLS failure means not-deployed.
    # follow_redirects=False makes 3xx a non-success (an HTTPError, so it
    # keeps polling unless any_status) — used by deploy_web.py's post-swap
    # probes, where a redirect to another (healthy) host must not count as
    # proof the app host served the release.
    context = http_ssl_context()
    handlers: list[urllib.request.BaseHandler] = []
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not follow_redirects:
        handlers.append(_NoRedirectHandler())
    opener = urllib.request.build_opener(*handlers) if handlers else None
    deadline = time.monotonic() + timeout_sec
    cert_error: ssl.SSLCertVerificationError | None = None
    while time.monotonic() < deadline:
        try:
            with (
                urllib.request.urlopen(url, timeout=5, context=context)
                if opener is None
                else opener.open(url, timeout=5)
            ) as resp:
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
        # Which of {local trust store, served certificate} is at fault is not
        # decidable from the exception — both raise CERTIFICATE_VERIFY_FAILED.
        # diagnose() settles it with a control probe before printing advice.
        failure = tls_preflight.diagnose(url, context=context)
        if failure is not None:
            report_probe_failure(failure)
            # cert_error was latched by ANY poll iteration, so the state at the
            # deadline can be something else entirely. Only the two certificate
            # verdicts may raise CertVerificationError — callers read that
            # class as "do not auto-rollback".
            if failure.kind in ("tls_local", "tls_server"):
                raise CertVerificationError(f"{url}: {failure.detail}")
            raise ClassifiedDeployError(f"{url}: {failure.detail}")
        # Latched a cert error while polling, but the classifier could not
        # reproduce it. The message must stay non-empty: callers turn an empty
        # DeployError into "details already printed", and nothing was.
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

# Each fact is captured first and echoed only if non-empty, because piping a
# FAILED openssl straight into sha256 yields e3b0c442… — the hash of empty
# input — for BOTH cert and key, so two unreadable PEMs would compare equal and
# silently pass the pair check.
_ORIGIN_CERT_PROBE = f"""
if [ -f {ORIGIN_CERT} ] && [ -f {ORIGIN_KEY} ]; then
    echo "ORIGIN_PRESENT=1"
    origin_sans=$(openssl x509 -in {ORIGIN_CERT} -noout -ext subjectAltName 2>/dev/null | tr '\\n' ' ')
    origin_cert_pub=$(openssl x509 -in {ORIGIN_CERT} -noout -pubkey 2>/dev/null)
    origin_key_pub=$(openssl pkey -in {ORIGIN_KEY} -pubout -passin pass: 2>/dev/null)
    [ -n "$origin_sans" ] && echo "ORIGIN_SANS=$origin_sans"
    [ -n "$origin_cert_pub" ] && echo "ORIGIN_CERT_PUB=$(printf '%s' "$origin_cert_pub" | openssl sha256 | awk '{{print $NF}}')"
    [ -n "$origin_key_pub" ] && echo "ORIGIN_KEY_PUB=$(printf '%s' "$origin_key_pub" | openssl sha256 | awk '{{print $NF}}')"
else
    echo "ORIGIN_CERT_MISSING=1"
fi
"""


def build_preflight_script(tls_mode: str) -> str:
    # Batched preflight: one SSH round-trip exercises the transport, reports
    # the remote user, and flags every failing prereq at once — so the operator
    # fixes them in a single pass instead of discovering them one connection at
    # a time. If ssh fails to even reach the remote bash, that surfaces as a
    # separate reachability error (the empty-errs branch in remote_preflight).
    # The origin-cert probe rides the same round-trip for the same reason: the
    # server rate-limits at 6 connections / 30s.
    origin = _ORIGIN_CERT_PROBE if tls_mode == "CloudflareOrigin" else ""
    return f"""set +e
user=$(whoami)
echo "USER=$user"
errs=""
[ -d {REMOTE_BASE} ]          || errs="$errs BASE_MISSING"
[ -w {REMOTE_BASE} ]          || errs="$errs BASE_NOT_WRITABLE"
[ -f {REMOTE_BASE}/.env ]     || errs="$errs ENV_MISSING"
docker info >/dev/null 2>&1 || errs="$errs DOCKER"
{origin}
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
    reload_args = "reload --force" if tls_mode == "CloudflareOrigin" else "reload"
    # One SSH round-trip does: (a) validate the incoming Caddyfile in a
    # throwaway container, (b) copy Caddyfile.<TlsMode> -> Caddyfile so
    # docker-compose can mount it, (c) compose up (streaming), (d) caddy
    # reload, (e) compose ps.
    # Validate BEFORE cp: the auto-rollback path swaps source dirs only, so
    # a rejected config copied over the live Caddyfile would stay mounted —
    # the running caddy keeps its in-memory config, but the next container
    # or host restart would take down all ingress. Validating the candidate
    # file first means the live Caddyfile is never replaced by a config the
    # reload would refuse. The throwaway container uses the same image tag
    # compose runs, so validation and runtime agree on the Caddy version.
    # Strict `test -f` + `cp -f`: if Caddyfile.<TlsMode> is missing (e.g.
    # --skip-build on a never-deployed TLS mode), the deploy fails loudly
    # instead of silently running the wrong TLS mode against a stale
    # Caddyfile. The guard must run BEFORE the docker-run mount: `-v` with a
    # missing host source creates it as a root-owned DIRECTORY, which would
    # poison every later deploy of that TLS mode until manually removed.
    # The reload step exists because compose never restarts a running caddy
    # container for a bind-mounted file's CONTENT change — without it a
    # Caddyfile-only deploy silently keeps serving the old config. Reload is
    # atomic (a parse failure keeps the old config live and fails this
    # command loudly). When compose just recreated the container the reload is
    # a harmless re-read.
    # stream_close_delay on both proxied site blocks (apex/api and app) does
    # NOT spare established WebSockets from a reload — Caddy's docs are
    # explicit that it "delays streaming requests such as WebSockets from being
    # forcibly closed when the config is unloaded". It buys a 5-minute grace,
    # then every stream registered against the unloaded config is closed, all
    # on the same timer. So a reload that actually swaps config is a DEFERRED
    # mass disconnect, not a free one.
    # That is why --force is scoped to CloudflareOrigin rather than always on.
    # There it is unavoidable: Caddy diffs the ADAPTED JSON and certificates
    # are referenced by path, so swapping tls/origin-*.pem in place leaves the
    # config byte-identical and a plain reload logs "config is unchanged",
    # does nothing, and keeps serving the old certificate from memory — while
    # reading like success. LetsEncrypt mode loads no certificate from a path
    # at all (that Caddyfile has zero `tls` directives), so forcing there would
    # buy nothing and would convert every no-op deploy into a disconnect of
    # every agent tunnel and client socket.
    # The tls/ mount mirrors the compose caddy service: CloudflareOrigin's
    # `tls <cert> <key>` directives load the PEMs at provision time, so
    # validate fails without them. tls/ always exists (bootstrap and the
    # extract script both create it), but keep the test -d guard so a
    # missing dir fails loudly instead of docker creating it root-owned.
    return (
        f"cd {REMOTE_BASE}"
        f" && test -f Caddyfile.{tls_mode} && test -d tls"
        f" && docker run --rm"
        f" -v {REMOTE_BASE}/Caddyfile.{tls_mode}:/etc/caddy/Caddyfile:ro"
        f" -v {REMOTE_BASE}/tls:/etc/caddy/tls:ro"
        f" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile"
        f" && cp -f Caddyfile.{tls_mode} Caddyfile"
        f" && docker compose -f docker-compose.prod.yml {up_args}"
        f" && docker compose -f docker-compose.prod.yml exec -T caddy"
        f" caddy {reload_args} --config /etc/caddy/Caddyfile"
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


def remote_preflight(tls_mode: str) -> tuple[str, list[tls_preflight.TlsWarning]]:
    probe = invoke_ssh(build_preflight_script(tls_mode))
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

    warnings: list[tls_preflight.TlsWarning] = []
    if tls_mode == "CloudflareOrigin":
        origin = tls_preflight.parse_origin_probe(probe.output.splitlines())
        if not origin.present:
            warn(f"{ORIGIN_CERT} / {ORIGIN_KEY} missing — CloudflareOrigin mode needs them")
        else:
            warnings = tls_preflight.origin_cert_warnings(origin, DOMAIN)
            for warning in warnings:
                report_tls_warning(warning)
            # Each half is asserted only where the evidence for it exists. An
            # unreadable PEM or a missing openssl yields no facts, and saying
            # "does not shadow" there would be a false all-clear on exactly the
            # cert this check exists to catch.
            kinds = {w.kind for w in warnings}
            if not origin.sans_read:
                warn(
                    f"Could not read {ORIGIN_CERT} SANs on the server — the "
                    f"wildcard-shadow check did NOT run. Verify openssl is "
                    f"installed and the PEMs are readable by '{ssh_user}'."
                )
            elif "shadow" not in kinds:
                ok(f"Origin cert SANs do not shadow app.{DOMAIN}")
            if not (origin.cert_pubkey and origin.key_pubkey):
                warn(
                    "Could not derive both public keys on the server — the "
                    "cert/key pair check did NOT run."
                )
            elif "pair" not in kinds:
                ok("Origin cert and key are a matching pair")
    return ssh_user, warnings


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


def diagnose_local_tls(urls: tuple[str, ...]) -> int:
    """Classify every host this workstation talks to. Returns a failure count."""
    step("Local TLS trust")
    context = http_ssl_context()
    control = tls_preflight.control_probe(context)
    failures = 0
    if control.verified_url is not None:
        ok(f"Control probe passed — this Python verifies public HTTPS ({control.verified_url})")
    elif control.reached:
        fail("Control probe FAILED — this Python cannot verify any HTTPS")
        for line in tls_preflight.certifi_remedy().splitlines():
            dim(f"  {line}")
        failures += 1
    else:
        warn("Control probe INCONCLUSIVE — no control host was reachable")
        dim("  Local-vs-server attribution cannot be settled; the served")
        dim("  certificate is reported below regardless.")
        failures += 1

    for url in urls:
        step(f"Probing {url}")
        # Probe every host even when the control probe failed. control_verified
        # is passed only when it was actually settled, so re-running it per URL
        # (which walks every address of every control host) is avoided without
        # asserting an answer that was not obtained.
        failure = tls_preflight.diagnose(
            url,
            context=context,
            control_verified=True if control.verified_url is not None else None,
        )
        if failure is None:
            ok(f"{url} verified")
        else:
            report_probe_failure(failure)
            failures += 1
    return failures


def run_tls_diagnosis(tls_mode: str) -> int:
    failures = diagnose_local_tls((HEALTH_URL, APP_URL))
    step("Server-side TLS material")
    if tls_mode != "CloudflareOrigin":
        # Say so rather than printing an empty section: the origin-cert checks
        # are the reason most operators reach for this command.
        warn(
            f"Origin-cert checks skipped — they only apply to CloudflareOrigin "
            f"mode (this run is --tls-mode {tls_mode}). Re-run with "
            f"--tls-mode CloudflareOrigin to check the origin cert."
        )
        return 1 if failures else 0
    if shutil.which("ssh") is None:
        # run() short-circuits to here before check_local_tools(), so guard the
        # one tool the server-side section needs rather than tracebacking.
        warn("Skipped: ssh not found in PATH, so the origin cert cannot be read")
        return 1 if failures else 0
    try:
        _, warnings = remote_preflight(tls_mode)
    except DeployError:
        warn("Skipped: the ssh preflight failed (see above)")
        return 1
    return 1 if failures or warnings else 0


def run(args: argparse.Namespace) -> int:
    if args.diagnose_tls:
        return run_tls_diagnosis(args.tls_mode)

    # The re-print below is the entire compensating control for TLS findings
    # not failing the deploy, so it has to survive every exit path. _run_deploy
    # fills this list in place.
    tls_warnings: list[tls_preflight.TlsWarning] = []
    try:
        return _run_deploy(args, tls_warnings)
    finally:
        if tls_warnings:
            step("Outstanding TLS warnings")
            for warning in tls_warnings:
                report_tls_warning(warning)


def _run_deploy(
    args: argparse.Namespace, tls_warnings: list[tls_preflight.TlsWarning]
) -> int:
    caddy_file = DEPLOY_DIR / f"Caddyfile.{args.tls_mode}"

    step("Preflight")
    check_local_tools()
    check_repo_artifacts(caddy_file)
    if not args.force and not args.rollback:
        check_clean_tree()
    _, warnings = remote_preflight(args.tls_mode)
    tls_warnings.extend(warnings)

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
            "  python deploy/prod/deploy_central.py --diagnose-tls --tls-mode CloudflareOrigin   # classify a TLS failure, deploy nothing\n"
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
    parser.add_argument(
        "--diagnose-tls",
        action="store_true",
        help="classify TLS failures for the relay and SPA hosts, then exit without deploying",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("deploy_central.py"):
        return 1
    args = build_parser().parse_args(argv)

    enable_ansi()

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
