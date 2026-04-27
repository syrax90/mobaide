"""Entry point for ``claude-agent`` and ``python -m claude_mobile``.

Subcommands:

* ``run``        — start the FastAPI app (default; what ``python -m claude_mobile``
                   has always done).
* ``update``     — force an immediate manifest check and apply if a new
                   version is rolled out.
* ``--version``  — print ``__version__`` and exit.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import uvicorn

from claude_mobile import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-agent",
        description="Mobaide agent — terminal session manager.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Start the agent (default).")
    sub.add_parser("update", help="Check for an update and apply if available.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if command == "run":
        _run_agent()
    elif command == "update":
        sys.exit(_run_update())
    else:
        parser.print_help()
        sys.exit(2)


def _run_agent() -> None:
    from claude_mobile.agent.app import create_app

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
    from claude_mobile.agent.install_paths import (
        default_prefix,
        read_trust_public_key,
    )
    from claude_mobile.agent.updater import InstallLayout

    layout = InstallLayout(prefix=default_prefix())
    public_key_b64 = read_trust_public_key(layout)
    if public_key_b64 is None:
        sys.stderr.write(
            f"claude-agent update: missing public key at {layout.trust_public_key}\n"
        )
        return 3

    manifest_url = os.environ.get("CLAUDE_AGENT_MANIFEST_URL")
    if not manifest_url:
        sys.stderr.write(
            "claude-agent update: CLAUDE_AGENT_MANIFEST_URL is not set; "
            "this CLI is normally invoked via the relay-driven auto-updater\n"
        )
        return 3

    return asyncio.run(
        _drive_update(layout, public_key_b64, manifest_url, force=True)
    )


async def _drive_update(layout, public_key_b64, manifest_url, *, force) -> int:
    import json
    import urllib.request

    from claude_mobile.agent.updater import StagedBinaryUpdater

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


if __name__ == "__main__":
    main()
