"""Entry point for ``mobaide-agent`` and ``python -m mobaide``.

Subcommands:

* ``run``        — start the FastAPI app (default; what ``python -m mobaide``
                   has always done).
* ``update``     — force an immediate manifest check and apply if a new
                   version is rolled out.
* ``pair``       — write ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` into the
                   agent's ``.env`` after pairing in the mobile app.
* ``--version``  — print ``__version__`` and exit.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from mobaide import __version__


# Restrictive enough to refuse newline injection (CR/LF/NUL) and shell
# metacharacters, lenient enough to allow base64-url, hex, UUIDs, and
# colon-prefixed tokens that some auth schemes generate.
_AGENT_CRED_RE = re.compile(r"^[A-Za-z0-9_\-\.:+/=]{1,1024}$")
_AGENT_RESTART_TIMEOUT_S = 15
_AGENT_STOP_TIMEOUT_S = 30
_WINDOWS_STOP_POLL_INTERVAL_S = 0.5
_WINDOWS_STOP_POLL_TIMEOUT_S = 10.0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mobaide-agent",
        description="Mobaide agent — terminal session manager.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Start the agent (default).")
    sub.add_parser("update", help="Check for an update and apply if available.")

    pair = sub.add_parser(
        "pair",
        help="Write relay credentials to <prefix>/mobaide_agent/.env.",
    )
    pair.add_argument("--id", required=True, help="Agent id from mobile app.")
    # Three mutually-exclusive token sources. The CLI argv form (``--token``)
    # leaks via /proc/<pid>/cmdline and shell history; ``--token-file`` and
    # ``--token-stdin`` are the recommended forms for shared hosts.
    token_src = pair.add_mutually_exclusive_group(required=True)
    token_src.add_argument(
        "--token", help="Agent token (NOTE: visible in process argv).",
    )
    token_src.add_argument(
        "--token-file",
        help="Path to a file containing the token (one line; whitespace stripped).",
    )
    token_src.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the token from stdin (one line; whitespace stripped).",
    )
    pair.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    pair.add_argument(
        "--no-restart",
        action="store_true",
        help="Skip the best-effort service restart.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "run":
        _run_agent()
    elif command == "update":
        sys.exit(_run_update())
    elif command == "pair":
        sys.exit(_run_pair(args))
    else:
        parser.print_help()
        sys.exit(2)


def _run_agent() -> None:
    from mobaide.agent.app import create_app

    app = create_app()
    uvicorn.run(
        app,
        host=app.state.config.host,
        port=app.state.config.port,
    )


def _run_update() -> int:
    """Synchronous wrapper that drives the staged-binary updater once.

    Returns:
        0   manifest applied or no change.
        2   manifest signature failed (fail-closed).
        3   no manifest pointer / cannot reach central_server.
        4   active sessions; deferred (use ``--force`` to override).
    """
    from mobaide.agent.install_paths import (
        default_prefix,
        read_trust_public_key,
    )
    from mobaide.agent.updater import InstallLayout

    layout = InstallLayout(prefix=default_prefix())
    public_key_b64 = read_trust_public_key(layout)
    if public_key_b64 is None:
        sys.stderr.write(
            f"mobaide-agent update: missing public key at {layout.trust_public_key}\n"
        )
        return 3

    manifest_url = os.environ.get("MOBAIDE_AGENT_MANIFEST_URL")
    if not manifest_url:
        sys.stderr.write(
            "mobaide-agent update: MOBAIDE_AGENT_MANIFEST_URL is not set; "
            "this CLI is normally invoked via the relay-driven auto-updater\n"
        )
        return 3

    return asyncio.run(
        _drive_update(layout, public_key_b64, manifest_url, force=True)
    )


async def _drive_update(layout, public_key_b64, manifest_url, *, force) -> int:
    import json
    import urllib.request

    from mobaide.agent.updater import StagedBinaryUpdater

    def _fetch() -> str:
        with urllib.request.urlopen(manifest_url, timeout=30) as resp:
            return resp.read().decode("utf-8")

    raw = await asyncio.to_thread(_fetch)
    record = json.loads(raw)
    body = record.get("body", record)
    signature_b64 = record.get("signature_b64", "")
    latest_version = body.get("version", "")

    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version=__version__,
    )
    outcome = await updater.consider_manifest(
        manifest_url, signature_b64, latest_version, force=force,
    )
    return _outcome_exit_code(outcome)


def _outcome_exit_code(outcome: str) -> int:
    if outcome in ("no_change", "staged"):
        return 0
    if outcome == "signature_failed":
        return 2
    if outcome == "deferred":
        return 4
    return 1


_DEFAULT_RELAY_URL = "wss://mobaide.com"


def _resolve_token(args: argparse.Namespace) -> str | None:
    """Return the token from --token, --token-file, or stdin.

    Strips trailing whitespace (the common case for
    ``echo "$TOKEN" > token.txt``). Returns ``None`` if the file cannot be
    read or stdin is empty. Uses ``getattr`` so that hand-built test
    namespaces only need to set the source they're using.
    """
    token = getattr(args, "token", None)
    if token is not None:
        return token
    token_file = getattr(args, "token_file", None)
    if token_file is not None:
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            sys.stderr.write(
                f"mobaide-agent pair: cannot read --token-file: {exc}\n"
            )
            return None
    if getattr(args, "token_stdin", False):
        data = sys.stdin.read().strip()
        return data or None
    return None


def _run_pair(args: argparse.Namespace) -> int:
    """Write ``RELAY_AGENT_ID`` / ``RELAY_AGENT_TOKEN`` atomically.

    Returns:
        0   credentials written; restart succeeded or was skipped.
        2   ``.env`` does not exist — user must run the installer or
            ``python -m mobaide.agent.config init-env`` first.
        3   ``--token-file`` could not be read or token was empty.
        4   ``--id`` or token failed validation (control chars or unsupported
            characters that would corrupt the .env on write).
    """
    from mobaide.agent._env_writer import (
        atomic_write,
        env_path_for_prefix,
        parse_env_file,
        render_template,
        restrict_perms,
    )
    from mobaide.agent.install_paths import default_prefix

    token = _resolve_token(args)
    if token is None or token == "":
        sys.stderr.write(
            "mobaide-agent pair: token was empty (or --token-file unreadable).\n"
        )
        return 3

    if not _AGENT_CRED_RE.match(args.id):
        sys.stderr.write(
            "mobaide-agent pair: --id contains characters that are not allowed "
            "(control chars or shell metacharacters); refusing to write a "
            "potentially corrupt .env.\n"
        )
        return 4
    if not _AGENT_CRED_RE.match(token):
        sys.stderr.write(
            "mobaide-agent pair: token contains characters that are not allowed "
            "(control chars or shell metacharacters); refusing to write a "
            "potentially corrupt .env.\n"
        )
        return 4

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    target = env_path_for_prefix(prefix)
    if not target.exists():
        sys.stderr.write(
            f"mobaide-agent pair: {target} does not exist.\n"
            f"Run the installer, or:\n"
            f"    python -m mobaide.agent.config init-env --prefix {prefix}\n"
        )
        return 2

    existing_text = target.read_text(encoding="utf-8")
    existing = parse_env_file(existing_text)

    overrides: dict[str, str] = {
        "RELAY_AGENT_ID": args.id,
        "RELAY_AGENT_TOKEN": token,
    }
    if not existing.get("RELAY_SERVER_URL"):
        overrides["RELAY_SERVER_URL"] = _DEFAULT_RELAY_URL

    rendered = render_template(existing_text, overrides=overrides)
    atomic_write(target, rendered)
    restrict_perms(target)
    sys.stdout.write(
        f"[mobaide-agent] Wrote relay credentials to {target}.\n"
    )

    if args.no_restart:
        sys.stdout.write(
            "[mobaide-agent] Skipping restart (--no-restart). "
            "Restart the service manually to pick up the new credentials.\n"
        )
        return 0

    return _try_restart_service()


def _try_restart_service() -> int:
    """Best-effort service restart. Never fails the pair operation."""
    if sys.platform.startswith("linux"):
        return _try_restart_systemd()
    if sys.platform == "darwin":
        return _try_restart_launchd()
    if sys.platform == "win32":
        return _try_restart_windows_service()
    sys.stdout.write(
        "[mobaide-agent] No supervisor known for this platform; "
        "restart the agent process manually.\n"
    )
    return 0


def _run_with_timeout(
    cmd: list[str],
    *,
    timeout: int = _AGENT_RESTART_TIMEOUT_S,
) -> tuple[int, str, str]:
    """Run ``cmd``; return ``(returncode, stdout, stderr)``.

    On ``TimeoutExpired`` and ``OSError``, returns ``(-1, "", reason)`` so
    callers can fall through to the manual-command message uniformly.
    Wrapping every supervisor command this way ensures the ``pair`` verb
    cannot hang indefinitely on a stuck systemctl/launchctl/sc.exe call.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def _try_restart_systemd() -> int:
    if not _command_exists("systemctl"):
        sys.stdout.write(
            "[mobaide-agent] systemctl not on PATH; restart manually.\n"
        )
        return 0
    rc, _, _ = _run_with_timeout(
        ["systemctl", "is-enabled", "mobaide-agent"],
    )
    if rc != 0:
        sys.stdout.write(
            "[mobaide-agent] mobaide-agent.service is not registered; "
            "skipping restart.\n"
        )
        return 0
    rc, _, _ = _run_with_timeout(
        ["sudo", "-n", "systemctl", "restart", "mobaide-agent"],
    )
    if rc == 0:
        sys.stdout.write("[mobaide-agent] Restarted mobaide-agent.\n")
        return 0
    sys.stdout.write(
        "[mobaide-agent] Could not restart automatically; run:\n"
        "    sudo systemctl restart mobaide-agent\n"
    )
    return 0


def _try_restart_launchd() -> int:
    label = "com.mobaide.agent"
    rc, stdout, _ = _run_with_timeout(["id", "-u"])
    if rc != 0:
        sys.stdout.write(
            "[mobaide-agent] Could not resolve uid; restart manually.\n"
        )
        return 0
    uid = stdout.strip()
    target = f"gui/{uid}/{label}"
    rc, _, _ = _run_with_timeout(["launchctl", "kickstart", "-k", target])
    if rc == 0:
        sys.stdout.write(f"[mobaide-agent] Kickstarted {target}.\n")
        return 0
    sys.stdout.write(
        f"[mobaide-agent] Could not kickstart automatically; run:\n"
        f"    launchctl kickstart -k {target}\n"
    )
    return 0


def _try_restart_windows_service() -> int:
    name = "MobaideAgent"
    rc, _, _ = _run_with_timeout(["sc.exe", "query", name])
    if rc != 0:
        sys.stdout.write(
            f"[mobaide-agent] Service {name} is not registered; "
            "skipping restart.\n"
        )
        return 0
    # `sc.exe stop` is asynchronous — it submits a STOP control to SCM and
    # returns immediately. Issuing `sc.exe start` while the service is in
    # STOP_PENDING gets rejected with error 1056 ("service already running"),
    # which surfaces as a misleading "need admin?" message. Poll until the
    # service is fully STOPPED before attempting to start it again.
    rc, _, _ = _run_with_timeout(
        ["sc.exe", "stop", name], timeout=_AGENT_STOP_TIMEOUT_S,
    )
    stop_acceptable = rc in (0, 1062)
    if stop_acceptable:
        _wait_for_windows_service_stopped(name)
    rc, _, _ = _run_with_timeout(["sc.exe", "start", name])
    if stop_acceptable and rc == 0:
        sys.stdout.write(f"[mobaide-agent] Restarted {name} service.\n")
        return 0
    sys.stdout.write(
        f"[mobaide-agent] Could not restart automatically (need admin?); "
        "run from an elevated PowerShell:\n"
        f"    Restart-Service {name}\n"
    )
    return 0


def _wait_for_windows_service_stopped(name: str) -> None:
    """Poll ``sc.exe query`` until STATE shows STOPPED or timeout elapses.

    Best-effort: returns silently after the timeout regardless of state.
    The follow-up ``sc.exe start`` will then either succeed (state really
    did become STOPPED) or fail with 1056/1061, which the caller handles.
    """
    deadline = time.monotonic() + _WINDOWS_STOP_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        rc, stdout, _ = _run_with_timeout(["sc.exe", "query", name], timeout=5)
        if rc != 0:
            return
        if "STOPPED" in stdout.upper():
            return
        time.sleep(_WINDOWS_STOP_POLL_INTERVAL_S)


def _command_exists(name: str) -> bool:
    from shutil import which

    return which(name) is not None


if __name__ == "__main__":
    main()
