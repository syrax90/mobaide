"""Entry point for ``mobaide-agent`` and ``python -m mobaide``.

Subcommands:

* ``run``              — start the FastAPI app (default; what ``python -m
                         mobaide`` has always done).
* ``update``           — force an immediate manifest check and apply if a new
                         version is rolled out.
* ``pair-qr``          — initiate a QR pairing nonce, render the QR to the
                         terminal, and (with ``--wait``) block until the
                         mobile app claims it. Replaces the legacy
                         ``pair --id ... --token ...`` flow.
* ``pair <code>``      — redeem a short pairing code minted in the Mobaide
                         app and wait for the account holder to confirm
                         this host. The direction that survives an air gap:
                         nothing has to be copied *off* this machine.
* ``show-trust-fp``    — print the fingerprint of the pinned relay signing
                         key, for comparing against what the app shows.
* ``show-qr``          — re-render the most-recent pending QR (or initiate
                         a fresh one if expired). Helpful for SSH-attached
                         operators who lost the install-time render.
* ``re-pair``          — tear down the running agent, blank credentials in
                         ``.env``, and start a fresh ``pair-qr`` cycle.
                         Used when re-pairing to a different mobile account
                         or rotating an agent's relay credentials.
* ``fetch-trust-key``  — fetch the manifest-signing public key from
                         ``central_server`` and pin it at
                         ``<prefix>/trust/central_server.pub`` so the
                         auto-updater is configured. Bootstrap path for
                         existing installs that were set up before
                         install.sh learned to fetch the key automatically.
* ``voice``            — ``status | install | remove | benchmark`` for the
                         opt-in on-PC speech-to-text (see ``mobaide.voice.cli``).
* ``--version``        — print ``__version__`` and exit.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import platform
import shlex
import socket
import subprocess
import sys
import time
import urllib.error as urllib_error
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import uvicorn

from mobaide import __version__
from mobaide.agent.install_paths import (
    TRUST_KEY_ENDPOINT_PATH as _TRUST_KEY_ENDPOINT_PATH,
    trust_key_url_from_relay_server_url as _trust_key_url_from_relay_server_url,
)


_AGENT_RESTART_TIMEOUT_S = 15
_AGENT_STOP_TIMEOUT_S = 30
_WINDOWS_STOP_POLL_INTERVAL_S = 0.5
_WINDOWS_STOP_POLL_TIMEOUT_S = 10.0
_LOCAL_HEALTH_TIMEOUT_S = 3.0
# Matches install.ps1 / install.sh's wait for the service's pending file.
_SERVICE_PENDING_TIMEOUT_S = 60.0
_SERVICE_PENDING_POLL_S = 0.5

# Set ``creationflags=_WINDOWS_NO_WINDOW`` on every subprocess invocation
# on Windows. The agent runs under ``pythonw.exe`` via the Scheduled Task
# (no parent console), so a default ``subprocess.run`` of a console-mode
# executable like ``schtasks.exe`` / ``sc.exe`` would otherwise pop a
# transient console window into the user's session. ``CREATE_NO_WINDOW``
# suppresses that without affecting stdio inheritance (unlike
# ``DETACHED_PROCESS``, which severs handle inheritance and breaks
# ``capture_output``).
_WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mobaide-agent",
        description="Mobaide agent — terminal session manager.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Start the agent (default).")
    sub.add_parser("update", help="Check for an update and apply if available.")

    fetch_key = sub.add_parser(
        "fetch-trust-key",
        help=(
            "Fetch the manifest-signing public key from central_server "
            "and pin it locally so the auto-updater can verify rollouts."
        ),
    )
    fetch_key.add_argument(
        "--from-url",
        help=(
            "Override URL to fetch the trust key from. Default: derive "
            "https://<host>/api/v1/agent/trust_key from RELAY_SERVER_URL "
            "in <prefix>/mobaide_agent/.env."
        ),
    )
    fetch_key.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    fetch_key.add_argument(
        "--no-restart",
        action="store_true",
        help=(
            "Skip the best-effort service restart. The trust key is "
            "written, but the running agent will NOT pick it up — and "
            "the auto-updater will stay disabled — until the next "
            "supervisor restart."
        ),
    )

    pair_qr = sub.add_parser(
        "pair-qr",
        help=(
            "Initiate a QR pairing nonce and render it to the terminal. "
            "With --wait, block until the mobile app claims it."
        ),
    )
    pq_mode = pair_qr.add_mutually_exclusive_group()
    pq_mode.add_argument(
        "--print",
        action="store_true",
        help=(
            "Render the QR and exit immediately. Used by install scripts "
            "to keep service start non-blocking."
        ),
    )
    pq_mode.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Render the QR and block on the pairing WebSocket until the "
            "mobile claim arrives or the nonce expires."
        ),
    )
    pair_qr.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    pair_qr.add_argument(
        "--no-restart",
        action="store_true",
        help="Skip the best-effort service restart after a successful claim.",
    )

    pair = sub.add_parser(
        "pair",
        help=(
            "Redeem a pairing code shown in the Mobaide app and wait for "
            "the account holder to confirm this machine."
        ),
    )
    pair.add_argument(
        "code",
        help="The code the app displayed, e.g. WDJB-MJHT (case-insensitive).",
    )
    pair.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Skip the interactive 'pair this machine to <account>?' prompt. "
            "Required when stdin is not a terminal. Only pass this when the "
            "code came from your own Mobaide account: pairing hands whoever "
            "owns that account shell sessions on this machine."
        ),
    )
    pair.add_argument(
        "--force",
        action="store_true",
        help=(
            "Pair even though this host already has relay credentials. "
            "Without it an already-paired agent refuses, so a code sent by "
            "someone else cannot silently move a working host."
        ),
    )
    pair.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    pair.add_argument(
        "--no-restart",
        action="store_true",
        help="Skip the best-effort service restart after a successful pairing.",
    )

    show_trust_fp = sub.add_parser(
        "show-trust-fp",
        help=(
            "Print the fingerprint of the relay signing key pinned on this "
            "host, for comparison against the one the app shows."
        ),
    )
    show_trust_fp.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )

    show_qr = sub.add_parser(
        "show-qr",
        help=(
            "Re-render the most-recent pending QR; initiate a fresh nonce "
            "if the cached one has expired."
        ),
    )
    show_qr.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    show_qr.add_argument(
        "--require-pending",
        action="store_true",
        help=(
            "Only render if a pending-pairing file already exists; never "
            "initiate a new nonce. Used by installers that delegate the "
            "initiate call to the running agent service."
        ),
    )

    re_pair = sub.add_parser(
        "re-pair",
        help=(
            "Re-pair this agent and start a fresh QR pairing cycle. Default: "
            "blank only RELAY_AGENT_TOKEN and keep RELAY_AGENT_ID so the relay "
            "rotates the existing agent's token (same-owner re-pair). Use "
            "--fresh to also forget RELAY_AGENT_ID and first-pair as a NEW "
            "agent — required when the relay no longer has this agent."
        ),
    )
    re_pair.add_argument(
        "--prefix",
        help="Override install prefix (default: install_paths.default_prefix()).",
    )
    re_pair.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Forget RELAY_AGENT_ID too (not just the token) so the next claim "
            "registers a brand-new agent instead of re-registering the old "
            "one. Use this when pairing fails with HTTP 404 'Re-pair target "
            "agent does not exist' — the old agent_id is gone server-side "
            "(removed from the account, or the relay DB was redeployed)."
        ),
    )
    re_pair.add_argument(
        "--no-restart",
        action="store_true",
        help=(
            "Blank credentials in .env without stopping or restarting the "
            "service. The next agent boot picks up pairing-pending state."
        ),
    )
    voice = sub.add_parser(
        "voice",
        help="Manage on-PC voice input (speech-to-text): status, install, remove, benchmark.",
    )
    voice_sub = voice.add_subparsers(dest="voice_command", required=True)
    for name, help_text in (
        ("status", "Show whether voice input is installed and what it costs."),
        ("install", "Download, verify and enable voice input (about 680 MB)."),
        ("remove", "Turn voice input off and delete its files."),
        ("benchmark", "Measure transcription latency on this computer."),
    ):
        voice_cmd = voice_sub.add_parser(name, help=help_text)
        voice_cmd.add_argument(
            "--prefix",
            help="Override install prefix (default: install_paths.default_prefix()).",
        )
        voice_cmd.add_argument("--voice-dir", help="Use this voice directory instead.")
        if name == "status":
            voice_cmd.add_argument(
                "--json", action="store_true", help="Print the raw status JSON."
            )
        if name == "remove":
            voice_cmd.add_argument(
                "--yes", action="store_true", help="Do not ask for confirmation."
            )
        if name == "benchmark":
            voice_cmd.add_argument(
                "--seconds",
                default="10,30,60,115",
                help="Comma-separated dictation lengths to measure (default: 10,30,60,115).",
            )
    return parser


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        # Install WindowsSelectorEventLoopPolicy for the CLI-verb path.
        # Python 3.8+ defaults to WindowsProactorEventLoopPolicy on Windows,
        # but python-websockets has long-standing TLS-recv hangs / silent
        # data-loss under Proactor — the agent's pairing flow calls
        # ``await ws.recv()`` to receive an Ed25519-signed credential push
        # from the central server, and on Windows under Proactor that push
        # can be dropped after the mobile already paired successfully,
        # producing the "mobile shows Paired, agent .env stays blank"
        # symptom that the in-band WS-clear-pending fix alone cannot reach
        # (every reconnect re-hits the same Proactor recv bug). See
        # https://github.com/python-websockets/websockets/issues/1118 and
        # https://vstinner.github.io/asyncio-proactor-wsarecv-cancellation-data-loss.html
        # for the underlying issue, and the ``_run_agent`` site below for
        # the matching Runner-based override needed by the ``run``
        # subcommand path.
        #
        # The global policy installed here flows to every entry point that
        # creates its loop via ``asyncio.run`` / ``asyncio.get_event_loop``
        # — the CLI verbs (``pair-qr --wait``, ``fetch-trust-key``,
        # ``re-pair``, ``update``). It does NOT flow to uvicorn's
        # single-process ``run`` path: uvicorn 0.36+ constructs its loop
        # via ``asyncio.Runner(loop_factory=Config.get_loop_factory())``,
        # bypassing the policy entirely. The ``run`` subcommand handles
        # that separately in ``_run_agent``.
        #
        # WindowsSelectorEventLoopPolicy is backed by ``select()`` (capped
        # at 64 sockets — fine for the agent's workload). It does not
        # support ``asyncio.create_subprocess_exec``; the agent's one
        # caller of that API has been migrated to
        # ``asyncio.to_thread(subprocess.run, ...)`` in
        # ``mobaide.linear.worktree._run_git`` (so Linear-driven worktree
        # operations keep working on Windows under either loop policy).
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "run":
        _run_agent()
        return
    try:
        rc = _dispatch(parser, command, args)
    except PermissionError as exc:
        rc = _report_permission_denied(
            args, exc, sys.argv[1:] if argv is None else argv
        )
    sys.exit(rc)


def _dispatch(
    parser: argparse.ArgumentParser, command: str, args: argparse.Namespace
) -> int:
    if command == "update":
        return _run_update()
    if command == "pair-qr":
        return _run_pair_qr(args)
    if command == "pair":
        return _run_pair(args)
    if command == "show-trust-fp":
        return _run_show_trust_fp(args)
    if command == "show-qr":
        return _run_show_qr(args)
    if command == "re-pair":
        return _run_re_pair(args)
    if command == "fetch-trust-key":
        return _run_fetch_trust_key(args)
    if command == "voice":
        from mobaide.voice.cli import run as run_voice

        return run_voice(args)
    parser.print_help()
    return 2


def _report_permission_denied(
    args: argparse.Namespace, exc: PermissionError, argv: list[str]
) -> int:
    """Turn a PermissionError from a maintenance verb into advice.

    The ``mobaide-agent`` command is on PATH for every account, but the
    install belongs to one: on POSIX the agent's user owns the prefix and its
    0600 files; on Windows the prefix sits under Program Files and only an
    elevated process may write there.
    """
    from mobaide.agent.install_paths import default_prefix

    target = exc.filename or str(exc)
    sys.stderr.write(f"mobaide-agent: permission denied: {target}\n")
    if sys.platform == "win32":
        sys.stderr.write(
            "Run this command from an elevated terminal "
            "(right-click the terminal, Run as administrator).\n"
        )
        return 13
    prefix = Path(args.prefix) if getattr(args, "prefix", None) else default_prefix()
    try:
        owner = prefix.owner()
    except (OSError, KeyError, NotImplementedError):
        owner = ""
    if owner and owner != _current_user():
        sys.stderr.write(
            f"This agent is installed for the user '{owner}'. Run it as "
            f"that user:\n    sudo -u {owner} mobaide-agent {shlex.join(argv)}\n"
        )
    else:
        sys.stderr.write(
            f"Check the ownership and permissions of {target}.\n"
        )
    return 13


def _current_user() -> str:
    import getpass

    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return ""


def _run_agent() -> None:
    from mobaide.agent.app import create_app
    from mobaide.agent.restart import bind_server, requested_exit_code

    app = create_app()
    config = uvicorn.Config(
        app,
        host=app.state.config.host,
        port=app.state.config.port,
        # The /internal/hook request line carries the per-agent hook token as a
        # query param; uvicorn's access log records the full target including
        # the query string, so disable it to keep the token out of operational
        # logs (mirrors central_server/__main__.py). The in-app structured
        # middleware logs method/path/status without the query string.
        access_log=False,
    )
    server = uvicorn.Server(config)
    # A staged update stops this server through ``request_restart`` and the
    # process then exits with the requested code. uvicorn turns a raw
    # ``sys.exit(75)`` during startup into its own exit code 3, which the
    # Windows supervisor does not respawn.
    bind_server(server)
    try:
        _serve(server)
    except SystemExit:
        if requested_exit_code() is None:
            raise
    finally:
        bind_server(None)
    code = requested_exit_code()
    if code is not None:
        sys.exit(code)
    if not server.started:
        sys.exit(uvicorn.config.STARTUP_FAILURE)


def _serve(server: uvicorn.Server) -> None:
    if sys.platform == "win32":
        # uvicorn 0.36+ bypasses ``asyncio.get_event_loop_policy()`` in
        # single-process mode by calling
        # ``asyncio.Runner(loop_factory=Config.get_loop_factory())``, and on
        # Windows ``get_loop_factory()`` returns the bare
        # ``asyncio.ProactorEventLoop`` constructor — the policy installed
        # at the top of ``main()`` is therefore not honoured for the
        # ``run`` subcommand (the path the Windows service actually takes).
        # Drop down to the underlying ``uvicorn.Server.serve()`` coroutine
        # and run it on an ``asyncio.Runner`` we explicitly construct with
        # ``loop_factory=asyncio.SelectorEventLoop``. The global policy set
        # in ``main()`` still serves the CLI verbs (``pair-qr --wait`` and
        # friends) that go through ``asyncio.run`` directly. This is the
        # specific code path the user's pairing flow exercises, so the
        # event-loop fix has to land here to reach
        # ``run_pairing_monitor`` → ``PairingWsClient.wait_for_claim``.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
        return
    # POSIX: ``Server.run`` builds uvicorn's default loop and handles signals,
    # like ``uvicorn.run``; Ctrl+C ends the run quietly, as it does there.
    try:
        server.run()
    except KeyboardInterrupt:
        pass


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
            f"mobaide-agent update: missing public key at {layout.trust_public_key}.\n"
            f"Run 'mobaide-agent fetch-trust-key' on this host to bootstrap it "
            f"from central_server, or set MOBAIDE_AGENT_TRUST_KEY_B64=<base64-ed25519> "
            f"for an airgap install.\n"
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

    import mobaide._http as _http
    from mobaide.agent.updater import StagedBinaryUpdater

    def _fetch() -> str:
        with _http.urlopen(manifest_url, timeout=30) as resp:
            return resp.read().decode("utf-8")

    raw = await asyncio.to_thread(_fetch)
    record = json.loads(raw)
    body = record.get("body", record)
    signature_b64 = record.get("signature_b64", "")
    latest_version = body.get("version", "")

    # The explicit ``mobaide-agent update`` CLI verb builds its own updater
    # here, deliberately bypassing app._build_updater's dev-run guard
    # (``_is_running_managed_release``). It is an operator opt-in that requires
    # MOBAIDE_AGENT_MANIFEST_URL, so running it from a source checkout against
    # the install prefix is intentional rather than the accidental clobber the
    # in-process auto-updater guard prevents.
    updater = StagedBinaryUpdater(
        layout=layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version=__version__,
    )
    outcome = await updater.consider_manifest(
        body, signature_b64, latest_version, force=force,
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


def _run_fetch_trust_key(args: argparse.Namespace) -> int:
    """Fetch the manifest-signing public key and pin it locally.

    Returns:
        0   key written successfully (and supervisor restarted, unless
            ``--no-restart``).
        2   ``RELAY_SERVER_URL`` could not be resolved (no env file or no
            value), and ``--from-url`` was not supplied.
        3   the trust-key endpoint could not be reached or returned a
            non-JSON / malformed response.
        4   the fetched value did not validate as a base64-Ed25519 public
            key.
    """
    import base64
    import json
    import urllib.error

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    import mobaide._http as _http
    from mobaide.agent._env_writer import (
        atomic_write,
        env_path_for_prefix,
        parse_env_file,
    )
    from mobaide.agent.install_paths import default_prefix
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)

    url = getattr(args, "from_url", None)
    if not url:
        env_file = env_path_for_prefix(prefix)
        if not env_file.exists():
            sys.stderr.write(
                f"mobaide-agent fetch-trust-key: {env_file} does not exist.\n"
                f"Pass --from-url <central_server_base>{_TRUST_KEY_ENDPOINT_PATH} "
                f"or run: python -m mobaide.agent.config init-env\n"
            )
            return 2
        relay_server_url = parse_env_file(env_file).get("RELAY_SERVER_URL", "").strip()
        if not relay_server_url:
            sys.stderr.write(
                f"mobaide-agent fetch-trust-key: RELAY_SERVER_URL is empty in "
                f"{env_file}.\n"
                f"Pair the agent first, or pass --from-url explicitly.\n"
            )
            return 2
        try:
            url = _trust_key_url_from_relay_server_url(relay_server_url)
        except ValueError as exc:
            sys.stderr.write(f"mobaide-agent fetch-trust-key: {exc}\n")
            return 2

    sys.stdout.write(f"[mobaide-agent] Fetching trust key from {url}\n")
    try:
        with _http.urlopen(url, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        sys.stderr.write(
            f"mobaide-agent fetch-trust-key: cannot reach {url}: {exc}\n"
        )
        return 3
    try:
        record = json.loads(raw)
        public_key_b64 = record["public_key_b64"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        sys.stderr.write(
            f"mobaide-agent fetch-trust-key: malformed response from {url}: "
            f"{exc}\n"
        )
        return 3
    if not isinstance(public_key_b64, str) or not public_key_b64.strip():
        sys.stderr.write(
            f"mobaide-agent fetch-trust-key: response from {url} carried an "
            f"empty public_key_b64.\n"
        )
        return 3

    public_key_b64 = public_key_b64.strip()
    try:
        key_bytes = base64.b64decode(public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(key_bytes)
    except (ValueError, TypeError) as exc:
        sys.stderr.write(
            f"mobaide-agent fetch-trust-key: fetched value is not a valid "
            f"base64 Ed25519 public key: {exc}\n"
        )
        return 4

    # Trust file is a public key — keep default 0o644 perms so a service
    # running as a non-root user (the typical systemd setup, where the
    # install was done with sudo) can still read the file written by
    # root. ``atomic_write`` uses ``mkstemp`` which creates the file
    # 0o600 by default; explicitly chmod back to umask-respecting
    # behaviour after the rename. POSIX-only — Windows ACLs are
    # inherited from the parent ``trust/`` dir, which is fine here.
    target = layout.trust_public_key
    atomic_write(target, public_key_b64 + "\n")
    if sys.platform != "win32":
        try:
            os.chmod(target, 0o644)
        except OSError:
            pass
    sys.stdout.write(
        f"[mobaide-agent] Wrote trust key to {target}.\n"
    )

    if args.no_restart:
        sys.stdout.write(
            "[mobaide-agent] Skipping restart (--no-restart). The auto-updater "
            "stays disabled until the agent restarts; restart manually:\n"
        )
        if sys.platform.startswith("linux"):
            sys.stdout.write("    sudo systemctl restart mobaide-agent\n")
        elif sys.platform == "darwin":
            sys.stdout.write(
                "    launchctl kickstart -k gui/$(id -u)/com.mobaide.agent\n",
            )
        elif sys.platform == "win32":
            sys.stdout.write(
                "    Stop-ScheduledTask  -TaskName MobaideAgent\n"
                "    Start-ScheduledTask -TaskName MobaideAgent\n"
            )
        return 0

    return _try_restart_service(prefix)


def _run_pair_qr(args: argparse.Namespace) -> int:
    """Initiate a QR pairing nonce, render it, optionally wait for the claim.

    Returns:
        0   pairing rendered (``--print``) or claim received (``--wait``).
        2   ``.env`` missing or RELAY_SERVER_URL unset — operator must run
            ``init-env`` first and configure the relay URL.
        3   network error talking to the central server.
        4   trust key not pinned (run ``mobaide-agent fetch-trust-key``).
        5   trust-key fingerprint mismatch with the relay's current key.
        6   pairing WebSocket failed (signature, transport, TTL).
    """
    from mobaide.agent._env_writer import (
        env_path_for_prefix,
        parse_env_file,
    )
    from mobaide.agent.install_paths import (
        default_prefix,
        read_trust_public_key,
    )
    from mobaide.agent.pairing_monitor import (
        PairingMonitorError,
        initiate_pairing,
        write_credentials_to_env,
    )
    from mobaide.agent.pairing_qr import (
        compute_trust_key_fp,
        load_pending_pairing,
        parse_qr_payload,
        render_qr_to_terminal,
        save_pending_pairing,
        clear_pending_pairing,
        write_pairing_hints,
    )
    from mobaide.agent.pairing_ws import PairingWsClient, PairingWsError
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)

    env_file = env_path_for_prefix(prefix)
    if not env_file.exists():
        sys.stderr.write(
            f"mobaide-agent pair-qr: {env_file} does not exist.\n"
            f"Run: python -m mobaide.agent.config init-env\n"
        )
        return 2
    env_kv = parse_env_file(env_file)
    relay_server_url = env_kv.get("RELAY_SERVER_URL", "").strip()
    if not relay_server_url:
        sys.stderr.write(
            f"mobaide-agent pair-qr: RELAY_SERVER_URL is empty in {env_file}.\n"
            f"Set it before pairing.\n"
        )
        return 2
    current_agent_id = env_kv.get("RELAY_AGENT_ID", "").strip() or None

    pinned_pub_key = read_trust_public_key(layout)
    if not pinned_pub_key:
        sys.stderr.write(
            f"mobaide-agent pair-qr: trust key not pinned at "
            f"{layout.trust_public_key}.\n"
            f"Run: mobaide-agent fetch-trust-key\n"
        )
        return 4
    pinned_fp = compute_trust_key_fp(pinned_pub_key)

    pending = load_pending_pairing(layout)
    if pending is None or pending.trust_key_fp != pinned_fp:
        try:
            pending = initiate_pairing(
                relay_server_url,
                pinned_fp,
                hostname=socket.gethostname(),
                os_name=platform.system(),
                current_agent_id=current_agent_id,
            )
        except PairingMonitorError as exc:
            sys.stderr.write(f"mobaide-agent pair-qr: {exc}\n")
            return 5
        except urllib_error.HTTPError as exc:
            if exc.code != 429:
                sys.stderr.write(
                    f"mobaide-agent pair-qr: cannot reach central server: {exc}\n"
                )
                return 3
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = f" Retry in {retry_after}s." if retry_after else ""
            sys.stderr.write(
                "mobaide-agent pair-qr: the central server is rate-limiting "
                f"pairing requests from this network.{wait}\n"
            )
            return 3
        except (urllib_error.URLError, OSError) as exc:
            sys.stderr.write(
                f"mobaide-agent pair-qr: cannot reach central server: {exc}\n"
            )
            return 3
        save_pending_pairing(layout, pending)

    sys.stdout.write(
        "[mobaide-agent] Pairing QR — scan from the Mobaide mobile app:\n"
    )
    render_qr_to_terminal(pending.qr_payload)
    write_pairing_hints(pending.qr_payload)
    sys.stdout.write(
        f"[mobaide-agent] Pairing nonce expires at {pending.expires_at}\n"
    )

    if not args.wait:
        return 0

    parsed = parse_qr_payload(pending.qr_payload)
    if parsed is None:
        sys.stderr.write(
            "mobaide-agent pair-qr: cached pending payload is malformed\n"
        )
        clear_pending_pairing(layout)
        return 5
    timeout_s = max(1.0, parsed.exp_unix - time.time())

    sys.stdout.write(
        "[mobaide-agent] Waiting for the mobile app to claim the QR...\n"
    )
    try:
        claim = asyncio.run(
            PairingWsClient().wait_for_claim(
                ws_url=pending.ws_url,
                timeout_s=timeout_s,
                trust_pub_key_b64=pinned_pub_key,
                expected_relay_url=pending.relay_url,
            )
        )
    except PairingWsError as exc:
        sys.stderr.write(f"mobaide-agent pair-qr: {exc}\n")
        clear_pending_pairing(layout)
        return 6

    asyncio.run(write_credentials_to_env(env_file, claim))
    clear_pending_pairing(layout)
    sys.stdout.write(
        f"[mobaide-agent] Paired as agent_id={claim.agent_id} "
        f"({claim.agent_friendly_name}).\n"
    )
    if args.no_restart:
        return 0
    return _try_restart_service(prefix)


def _confirm_device_pairing(account_hint: str, hostname: str) -> bool:
    """Ask the operator whether to hand this machine to ``account_hint``.

    This prompt is the reversed flow's primary safeguard, not a courtesy.
    Because the *browser* mints the code, a confirmation shown only in the
    browser is answered by whoever minted it — so an attacker who talks
    someone into running this command with the attacker's code would be
    confirming their own request while the victim's machine becomes an
    agent they own. The account name has to be read here, on the terminal
    of the machine being given away.
    """
    sys.stdout.write(
        f"\n[mobaide-agent] Pair '{hostname}' with Mobaide account "
        f"{account_hint}?\n"
        "[mobaide-agent] That account will be able to open terminal "
        "sessions on this machine.\n"
        "[mobaide-agent] If you did not just request this code in your own "
        "Mobaide app, answer no.\n"
    )
    try:
        answer = input("[mobaide-agent] Pair this machine? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _run_pair(args: argparse.Namespace) -> int:
    """Redeem a device code and wait for the account holder's confirmation.

    Returns:
        0   paired.
        2   ``.env`` missing or RELAY_SERVER_URL unset.
        3   network error talking to the central server.
        4   trust key not pinned (run ``mobaide-agent fetch-trust-key``).
        5   trust-key fingerprint mismatch with the relay's current key.
        6   pairing WebSocket failed (signature, transport, TTL); the
            redemption stays on file so a re-run can reconnect to it.
        8   the relay refused the code (unknown, expired, already used).
        9   refused locally: already paired, or the operator declined.
    """
    from mobaide.agent._env_writer import (
        env_path_for_prefix,
        parse_env_file,
    )
    from mobaide.agent.install_paths import (
        default_prefix,
        read_trust_public_key,
    )
    from mobaide.agent.pairing_monitor import (
        DeviceCodeRejected,
        PairingMonitorError,
        accept_device_code,
        redeem_device_code,
        write_credentials_to_env,
    )
    from mobaide.agent.pairing_qr import (
        PendingDeviceCode,
        clear_pending_device_code,
        compute_trust_key_fp,
        device_code_fingerprint,
        load_pending_device_code,
        new_redeemer_id,
        save_pending_device_code,
    )
    from mobaide.agent.pairing_ws import PairingWsClient, PairingWsError
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)

    env_file = env_path_for_prefix(prefix)
    if not env_file.exists():
        sys.stderr.write(
            f"mobaide-agent pair: {env_file} does not exist.\n"
            f"Run: python -m mobaide.agent.config init-env\n"
        )
        return 2
    env_kv = parse_env_file(env_file)
    relay_server_url = env_kv.get("RELAY_SERVER_URL", "").strip()
    if not relay_server_url:
        sys.stderr.write(
            f"mobaide-agent pair: RELAY_SERVER_URL is empty in {env_file}.\n"
            f"Set it before pairing.\n"
        )
        return 2
    current_agent_id = env_kv.get("RELAY_AGENT_ID", "").strip() or None
    if (
        current_agent_id
        and env_kv.get("RELAY_AGENT_TOKEN", "").strip()
        and not args.force
    ):
        sys.stderr.write(
            "mobaide-agent pair: this host already has relay credentials. "
            "Re-pairing it would move it to whichever account issued the "
            "code. If that is what you want, re-run with --force; to hand "
            "the host to a different account, run 'mobaide-agent re-pair' "
            "first.\n"
        )
        return 9

    # Checked before the redeem, not after: redeeming spends the code, and
    # a run that could never show the confirmation prompt must not spend it.
    if not args.yes and not sys.stdin.isatty():
        sys.stderr.write(
            "mobaide-agent pair: stdin is not a terminal, so the "
            "confirmation prompt cannot be shown. Re-run with --yes only "
            "if the code came from your own Mobaide account.\n"
        )
        return 9

    pinned_pub_key = read_trust_public_key(layout)
    if not pinned_pub_key:
        sys.stderr.write(
            f"mobaide-agent pair: trust key not pinned at "
            f"{layout.trust_public_key}.\n"
            f"Run: mobaide-agent fetch-trust-key\n"
        )
        return 4
    pinned_fp = compute_trust_key_fp(pinned_pub_key)

    # Resume a redemption this host already won: the relay hands the nonce
    # out exactly once, so a run interrupted between redeem and claim can
    # only continue from the local record.
    code_fp = device_code_fingerprint(args.code)
    stored = load_pending_device_code(layout, code_fp)
    record = stored or PendingDeviceCode(
        redeemer_id=new_redeemer_id(), code_fp=code_fp
    )
    hostname = socket.gethostname()
    is_repair = bool(current_agent_id)

    if record.pending is None:
        save_pending_device_code(layout, record)
        try:
            pending, account_hint, is_repair = redeem_device_code(
                relay_server_url,
                pinned_fp,
                code=args.code,
                redeemer_id=record.redeemer_id,
                hostname=hostname,
                os_name=platform.system(),
                current_agent_id=current_agent_id,
            )
        except DeviceCodeRejected as exc:
            sys.stderr.write(f"mobaide-agent pair: {exc}\n")
            clear_pending_device_code(layout)
            return 8
        except PairingMonitorError as exc:
            sys.stderr.write(f"mobaide-agent pair: {exc}\n")
            clear_pending_device_code(layout)
            return 5
        except (urllib_error.URLError, OSError) as exc:
            sys.stderr.write(
                f"mobaide-agent pair: cannot reach central server: {exc}\n"
            )
            return 3

        if pending is None:
            sys.stderr.write(
                "mobaide-agent pair: this code was already redeemed from "
                "this machine, and the pairing nonce is not recoverable. "
                "Request a new code in the Mobaide app and run this "
                "command again.\n"
            )
            clear_pending_device_code(layout)
            return 8

        # Persisted before the prompt, not after: the prompt blocks on human
        # input, and a Ctrl-C or a dropped SSH session there would otherwise
        # leave a code the relay has already spent with no local nonce to
        # resume from. ``confirmed`` stays false, so the resume below still
        # has to ask.
        record = replace(record, pending=pending, account_hint=account_hint)
        save_pending_device_code(layout, record)
    else:
        sys.stdout.write(
            "[mobaide-agent] Resuming the pairing request this machine "
            "already started.\n"
        )

    # Runs on every path, including a resume. This prompt is the reversed
    # flow's primary control, so an interrupted run must not be able to slip
    # past it by coming back with a nonce already on disk.
    if not record.confirmed:
        account_hint = record.account_hint or "an unknown account"
        if not args.yes and not _confirm_device_pairing(account_hint, hostname):
            sys.stderr.write(
                "mobaide-agent pair: declined. The code has been used up "
                "and cannot be redeemed again.\n"
            )
            clear_pending_device_code(layout)
            return 9
        if args.yes:
            sys.stdout.write(
                f"[mobaide-agent] Pairing '{hostname}' with Mobaide account "
                f"{account_hint} (--yes).\n"
            )
        record = replace(record, confirmed=True)
        save_pending_device_code(layout, record)

    # Sent on every path that gets past the prompt, resume included, and
    # before the wait: until the relay has it the request still polls as
    # pending, so the account holder's confirm button does not exist and
    # nothing can be registered or rotated behind this operator's back.
    try:
        accept_device_code(
            relay_server_url, code=args.code, redeemer_id=record.redeemer_id
        )
    except DeviceCodeRejected as exc:
        sys.stderr.write(f"mobaide-agent pair: {exc}\n")
        clear_pending_device_code(layout)
        return 8
    except (urllib_error.URLError, OSError) as exc:
        sys.stderr.write(
            f"mobaide-agent pair: cannot reach central server: {exc}\n"
            f"mobaide-agent pair: the approval was not recorded. Re-run "
            f"'mobaide-agent pair {args.code}' to resume — it will not ask "
            "again.\n"
        )
        return 3

    if is_repair:
        sys.stdout.write(
            "[mobaide-agent] Re-pairing the existing agent "
            f"{current_agent_id}.\n"
        )

    pending = record.pending

    sys.stdout.write(
        "[mobaide-agent] Waiting for the pairing to be confirmed in the "
        f"Mobaide app (until {pending.expires_at}). Press Ctrl-C to "
        "abort.\n"
    )
    timeout_s = max(
        1.0, datetime.fromisoformat(pending.expires_at).timestamp() - time.time()
    )
    try:
        claim = asyncio.run(
            PairingWsClient().wait_for_claim(
                ws_url=pending.ws_url,
                timeout_s=timeout_s,
                trust_pub_key_b64=pinned_pub_key,
                expected_relay_url=pending.relay_url,
            )
        )
    except PairingWsError as exc:
        # The record deliberately survives a WebSocket failure. The relay
        # holds the claimed credentials in ``claimed_token_raw`` for a
        # reconnect on this same nonce, and the raw nonce exists nowhere
        # else — clearing it here would turn a dropped connection into a
        # burnt code, because a re-run would redeem under a fresh
        # ``redeemer_id`` and be refused. A resume whose nonce has since
        # expired is dropped by ``load_pending_device_code`` instead.
        sys.stderr.write(
            f"mobaide-agent pair: {exc}\n"
            f"mobaide-agent pair: this redemption is still on file. Re-run "
            f"'mobaide-agent pair {args.code}' before {pending.expires_at} "
            "to reconnect and pick up the credentials.\n"
        )
        return 6

    asyncio.run(write_credentials_to_env(env_file, claim))
    clear_pending_device_code(layout)
    sys.stdout.write(
        f"[mobaide-agent] Paired as agent_id={claim.agent_id} "
        f"({claim.agent_friendly_name}).\n"
    )
    if args.no_restart:
        return 0
    return _try_restart_service(prefix)


def _run_show_trust_fp(args: argparse.Namespace) -> int:
    """Print the fingerprint of the pinned relay signing key.

    Returns:
        0   fingerprint printed.
        4   trust key not pinned (run ``mobaide-agent fetch-trust-key``).
    """
    from mobaide.agent.install_paths import default_prefix
    from mobaide.agent.pairing_qr import read_pinned_trust_key_fp
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)
    fingerprint = read_pinned_trust_key_fp(layout)
    if fingerprint is None:
        sys.stderr.write(
            f"mobaide-agent show-trust-fp: no usable trust key at "
            f"{layout.trust_public_key}.\n"
            f"Run: mobaide-agent fetch-trust-key\n"
        )
        return 4
    sys.stdout.write(f"{fingerprint}\n")
    sys.stdout.write(
        f"[mobaide-agent] Pinned from {layout.trust_public_key}. This must "
        "match the relay fingerprint shown in the Mobaide app; if it does "
        "not, stop and investigate before pairing.\n"
    )
    return 0


def _run_show_qr(args: argparse.Namespace) -> int:
    """Re-render the most-recent pending QR or initiate a fresh nonce.

    If the agent already has both ``RELAY_AGENT_ID`` and
    ``RELAY_AGENT_TOKEN`` set, prints ``already paired`` and exits.

    With ``--require-pending`` the fall-through to ``pair-qr --print``
    (which initiates a new nonce) is disabled — the verb instead exits
    7 if no pending file is found. Used by the install scripts so the
    running agent service is the single initiator of the pairing
    nonce; two competing initiates would race for the same WebSocket
    endpoint.

    Without the flag, the agent answering on ``PORT`` is asked first. An
    agent still booting into pairing mode (right after ``re-pair``) is
    waited for, so this process does not initiate a nonce that the
    service then replaces. An agent that reports it is paired while
    ``.env`` holds no credentials started before they were blanked and
    would never answer the QR, so nothing is rendered.

    Returns:
        0   rendered, or already paired.
        7   no pending file to render (``--require-pending``, or the
            agent did not publish one in time).
        9   the running agent is still paired with its old credentials.
        Other codes come from the ``pair-qr --print`` fall-through.
    """
    from mobaide.agent._env_writer import env_path_for_prefix, parse_env_file
    from mobaide.agent.install_paths import default_prefix
    from mobaide.agent.pairing_qr import (
        load_pending_pairing,
        render_qr_to_terminal,
        write_pairing_hints,
    )
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)
    env_file = env_path_for_prefix(prefix)
    env_kv = parse_env_file(env_file) if env_file.exists() else None
    if env_kv is not None:
        if env_kv.get("RELAY_AGENT_ID", "").strip() and env_kv.get(
            "RELAY_AGENT_TOKEN", ""
        ).strip():
            sys.stdout.write(
                "[mobaide-agent] Already paired. Run 'mobaide-agent re-pair' "
                "to re-pair to a different account.\n"
            )
            return 0

    pending = load_pending_pairing(layout)
    require_pending = getattr(args, "require_pending", False)
    if (
        env_kv is not None
        and env_kv.get("RELAY_SERVER_URL", "").strip()
        and not require_pending
    ):
        health = _local_agent_health(env_kv)
        agent_pairing = health.get("pairing_pending") if health else None
        if agent_pairing is False:
            sys.stderr.write(
                "mobaide-agent show-qr: the agent running on this machine is "
                "still paired with the credentials it started with, but "
                f"{env_file} no longer has them, so it would never answer a "
                "pairing QR. Restart it into pairing mode by running "
                "'mobaide-agent re-pair' again, then run "
                "'mobaide-agent show-qr'.\n"
            )
            return 9
        if agent_pairing is True and pending is None:
            pending = _wait_for_service_pending_pairing(layout)
            if pending is None:
                sys.stderr.write(
                    "mobaide-agent show-qr: the agent is waiting to pair but "
                    f"has not published a pairing QR within "
                    f"{_SERVICE_PENDING_TIMEOUT_S:.0f}s; it most likely cannot "
                    "reach the relay and keeps retrying on its own. Check the "
                    "agent logs, then run 'mobaide-agent show-qr' again.\n"
                )
                return 7

    if pending is not None:
        sys.stdout.write(
            "[mobaide-agent] Pending pairing QR — scan from the Mobaide "
            "mobile app:\n"
        )
        render_qr_to_terminal(pending.qr_payload)
        write_pairing_hints(pending.qr_payload)
        sys.stdout.write(
            f"[mobaide-agent] Pairing nonce expires at "
            f"{pending.expires_at}\n"
        )
        return 0

    if require_pending:
        sys.stderr.write(
            "mobaide-agent show-qr: no pending-pairing file at "
            f"{layout.prefix / 'run' / 'pending_pairing.json'} and "
            "--require-pending is set; refusing to initiate a new nonce. "
            "Wait for the agent service to write the pending file, or "
            "drop --require-pending to initiate from this process.\n"
        )
        return 7

    fake_args = argparse.Namespace(
        prefix=str(prefix), print=True, wait=False, no_restart=True
    )
    return _run_pair_qr(fake_args)


def _local_agent_health(env_kv: dict[str, str]) -> dict | None:
    """The ``/api/health`` body of the agent on this host, or None.

    Dials 127.0.0.1 on the ``.env`` ``PORT`` (HOST is the bind address, not
    the contact address), as the installer does, over a direct connection so
    an ``HTTP_PROXY`` cannot intercept it. None when nothing answers or the
    answer is not an agent's health body.
    """
    import http.client
    import json

    try:
        port = int(env_kv.get("PORT", "").strip() or "8000")
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_LOCAL_HEALTH_TIMEOUT_S)
    try:
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        body = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    if not isinstance(body, dict) or "pairing_pending" not in body:
        return None
    return body


def _wait_for_service_pending_pairing(layout):
    """Poll for the pending-pairing file the agent service publishes."""
    from mobaide.agent.pairing_qr import load_pending_pairing

    sys.stdout.write(
        "[mobaide-agent] Waiting for the agent to publish its pairing QR...\n"
    )
    sys.stdout.flush()
    deadline = time.monotonic() + _SERVICE_PENDING_TIMEOUT_S
    while True:
        pending = load_pending_pairing(layout)
        if pending is not None or time.monotonic() >= deadline:
            return pending
        time.sleep(_SERVICE_PENDING_POLL_S)


def _run_re_pair(args: argparse.Namespace) -> int:
    """Blank relay credentials in .env and trigger a fresh QR pairing cycle.

    Two modes:

    * **default (same-owner re-pair)** — blanks only ``RELAY_AGENT_TOKEN``
      and preserves ``RELAY_AGENT_ID``. The boot autorun carries the
      retained id into the next initiate as ``current_agent_id`` so the
      relay re-registers (rotates the token for) the *existing* agent row.
      Correct only when the agent still exists on the relay.
    * **--fresh (first-pair)** — blanks BOTH ``RELAY_AGENT_ID`` and
      ``RELAY_AGENT_TOKEN`` so no ``current_agent_id`` is carried and the
      relay registers a brand-new agent. Use this when the relay no longer
      has this agent: the claim 404s with "Re-pair target agent does not
      exist" because the id was removed from the account or the relay DB
      was redeployed. The default mode cannot recover from that — it keeps
      re-sending the dead id and 404s on every cycle.

    Authorization model: shell access on the agent host is sufficient.
    On platforms with a registered service supervisor we restart so the
    boot autorun picks up ``pairing_pending`` immediately; otherwise we
    print a manual restart hint.

    Returns:
        0   .env rewritten; restart attempted (or skipped per --no-restart).
        2   .env missing.
    """
    from mobaide.agent._env_writer import (
        blank_relay_credentials,
        env_path_for_prefix,
    )
    from mobaide.agent.install_paths import default_prefix
    from mobaide.agent.pairing_qr import clear_pending_pairing
    from mobaide.agent.updater import InstallLayout

    prefix = Path(args.prefix) if args.prefix else default_prefix()
    layout = InstallLayout(prefix=prefix)
    env_file = env_path_for_prefix(prefix)
    if not env_file.exists():
        sys.stderr.write(
            f"mobaide-agent re-pair: {env_file} does not exist.\n"
            f"Run: python -m mobaide.agent.config init-env\n"
        )
        return 2

    fresh = getattr(args, "fresh", False)
    blank_relay_credentials(env_file, clear_agent_id=fresh)
    clear_pending_pairing(layout)
    if fresh:
        sys.stdout.write(
            "[mobaide-agent] Blanked RELAY_AGENT_ID and RELAY_AGENT_TOKEN in "
            f"{env_file}; the next pairing claim first-pairs as a brand-new "
            "agent.\n"
        )
    else:
        sys.stdout.write(
            "[mobaide-agent] Blanked RELAY_AGENT_TOKEN in "
            f"{env_file}; RELAY_AGENT_ID is preserved so the next pairing "
            "claim rotates the existing agent's token. If pairing then 404s "
            "with 'Re-pair target agent does not exist', the relay no longer "
            "has this agent — re-run: mobaide-agent re-pair --fresh\n"
        )

    if args.no_restart:
        sys.stdout.write(
            "[mobaide-agent] Skipping restart (--no-restart). The next "
            "agent boot enters pairing-pending; run "
            "'mobaide-agent show-qr' to render a fresh QR.\n"
        )
        return 0
    rc = _try_restart_service(prefix)
    sys.stdout.write(
        "[mobaide-agent] After the service restarts, run "
        "'mobaide-agent show-qr' to render the new QR for the mobile app.\n"
    )
    return rc


def _try_restart_service(prefix: Path | None = None) -> int:
    """Best-effort service restart. Never fails the pair operation."""
    if sys.platform.startswith("linux"):
        return _try_restart_systemd()
    if sys.platform == "darwin":
        return _try_restart_launchd()
    if sys.platform == "win32":
        return _try_restart_windows_service(prefix)
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
            creationflags=_WINDOWS_NO_WINDOW,
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


def _try_restart_windows_service(prefix: Path | None = None) -> int:
    """Restart the agent on Windows.

    Probes for the per-user Scheduled Task first (post-migration shape);
    falls back to the legacy SCM service detection during the upgrade
    window where a host may still have the old service registered.
    """
    name = "MobaideAgent"
    rc, _, _ = _run_with_timeout(["schtasks.exe", "/Query", "/TN", name])
    if rc == 0:
        return _restart_windows_scheduled_task(name, prefix)
    rc, _, _ = _run_with_timeout(["sc.exe", "query", name])
    if rc == 0:
        return _restart_windows_scm_service(name)
    sys.stdout.write(
        f"[mobaide-agent] No {name} Scheduled Task or service "
        "registered; skipping restart.\n"
    )
    return 0


def _restart_windows_scheduled_task(name: str, prefix: Path | None = None) -> int:
    """End and re-Run the Scheduled Task ``name``.

    ``schtasks.exe /End`` terminates any running instance and is
    best-effort (rc 1 is treated as "nothing was running"). We then
    poll for ``Status: Ready`` so the subsequent ``/Run`` does not
    race with the still-tearing-down instance, then ``/Run`` to start
    a fresh one.

    ``/End`` terminates only the supervisor. One that predates its
    kill-on-close Job Object leaves the launcher and agent running with
    the port and the relay credentials they booted with, and the new
    instance then exits on the port clash while the stale agent keeps
    serving — so a re-pair renders a QR that no agent is waiting on. Any
    process still running from the install is therefore stopped before
    ``/Run``.
    """
    from mobaide.agent.install_paths import default_prefix

    install = os.path.abspath(prefix if prefix is not None else default_prefix())
    procs = _windows_processes()
    try:
        inside = procs.runs_inside_install(procs.list_processes(), os.getpid(), install)
    except OSError:
        inside = False
    if inside:
        sys.stdout.write(
            f"[mobaide-agent] Not restarting {name}: this command is running "
            "inside one of the agent's own terminal sessions, and stopping the "
            "agent would end it before it could start the agent again. Run the "
            "command again from a terminal outside Mobaide.\n"
        )
        return 0
    _run_with_timeout(
        ["schtasks.exe", "/End", "/TN", name], timeout=_AGENT_STOP_TIMEOUT_S,
    )
    _wait_for_windows_scheduled_task_ready(name)
    _stop_leftover_windows_agent(install)
    rc, _, _ = _run_with_timeout(["schtasks.exe", "/Run", "/TN", name])
    if rc == 0:
        sys.stdout.write(f"[mobaide-agent] Restarted {name} Scheduled Task.\n")
        return 0
    sys.stdout.write(
        f"[mobaide-agent] Could not restart automatically (need admin?); "
        "run from an elevated PowerShell:\n"
        f"    Stop-ScheduledTask  -TaskName {name}\n"
        f"    Start-ScheduledTask -TaskName {name}\n"
    )
    return 0


def _windows_processes():
    from mobaide.agent import windows_processes

    return windows_processes


def _stop_leftover_windows_agent(install: str) -> None:
    """Stop every process still running from ``install`` and wait for it.

    Best-effort: a process that will not stop is reported, and the restart
    goes ahead.
    """
    procs = _windows_processes()
    try:
        pids = procs.install_process_ids(
            procs.list_processes(), install, keep_pid=os.getpid(),
        )
        if not pids:
            return
        remaining = procs.terminate(pids, timeout_s=_WINDOWS_STOP_POLL_TIMEOUT_S)
    except OSError as exc:
        sys.stdout.write(
            f"[mobaide-agent] Could not check for agent processes left "
            f"running from {install}: {exc}\n"
        )
        return
    if remaining:
        ids = ", ".join(str(pid) for pid in remaining)
        sys.stdout.write(
            f"[mobaide-agent] Could not stop leftover agent process(es) {ids}; "
            "the restarted agent may fail to start. Stop them from an elevated "
            f"PowerShell:\n    Stop-Process -Force -Id {ids}\n"
        )
        return
    sys.stdout.write(
        f"[mobaide-agent] Stopped {len(pids)} leftover agent process(es) "
        f"running from {install}.\n"
    )


def _restart_windows_scm_service(name: str) -> int:
    """Legacy SCM service restart path (pre-migration upgrade window).

    `sc.exe stop` is asynchronous — it submits a STOP control to SCM and
    returns immediately. Issuing `sc.exe start` while the service is in
    STOP_PENDING gets rejected with error 1056 ("service already running"),
    which surfaces as a misleading "need admin?" message. Poll until the
    service is fully STOPPED before attempting to start it again.
    """
    rc, _, _ = _run_with_timeout(
        ["sc.exe", "stop", name], timeout=_AGENT_STOP_TIMEOUT_S,
    )
    stop_acceptable = rc in (0, 1062)
    if stop_acceptable:
        _wait_for_windows_service_stopped(name)
    rc, _, _ = _run_with_timeout(["sc.exe", "start", name])
    if stop_acceptable and rc == 0:
        sys.stdout.write(
            f"[mobaide-agent] Restarted legacy {name} service. "
            "Re-run the installer to migrate to the Scheduled Task.\n"
        )
        return 0
    sys.stdout.write(
        f"[mobaide-agent] Could not restart legacy service automatically; "
        "re-run the installer to migrate to the Scheduled Task, or from "
        "an elevated PowerShell:\n"
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


def _wait_for_windows_scheduled_task_ready(name: str) -> None:
    """Poll ``schtasks.exe /Query`` until ``Status:`` reads ``Ready``.

    Best-effort: returns silently after the timeout. The follow-up
    ``schtasks.exe /Run`` will then either succeed (task is really
    Ready) or fail with a non-zero exit, which the caller handles.
    """
    deadline = time.monotonic() + _WINDOWS_STOP_POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        rc, stdout, _ = _run_with_timeout(
            ["schtasks.exe", "/Query", "/TN", name, "/FO", "LIST", "/V"],
            timeout=5,
        )
        if rc != 0:
            return
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Status:") and "Ready" in stripped:
                return
        time.sleep(_WINDOWS_STOP_POLL_INTERVAL_S)


def _command_exists(name: str) -> bool:
    from shutil import which

    return which(name) is not None


if __name__ == "__main__":
    main()
