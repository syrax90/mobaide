#!/usr/bin/env python3
"""deploy_all.py — Ship the chosen Mobaide components to production in one run.

Usage:
    python deploy/prod/deploy_all.py                       # pick components in a checkbox menu
    python deploy/prod/deploy_all.py --only server,web     # skip the menu
    python deploy/prod/deploy_all.py --only web --yes      # no prompts at all
    python deploy/prod/deploy_all.py --dry-run             # print the plan, run nothing

Components and the command each one runs, in this fixed order:

    server   deploy_central.py --tls-mode CloudflareOrigin --force
    agent    release_agent.py <version from .release-please-manifest.json>
    web      deploy_web.py
    android  release_android.py <version from .release-please-manifest.json>

iOS is not here: it ships from Xcode.

The order is back to front: the relay first, the web SPA after it (a
cookie-era SPA on an old relay silently ends every browser session), the
store client last. A failed step stops the run, because the later steps
depend on the earlier ones.

Pre-selection. A box is ticked when production runs an older version than
the manifest on HEAD. The live version comes from the best source available:

    server   GET https://mobaide.com/api/version          -> server_version
    agent    GET https://api.mobaide.com/api/v1/agent/manifest.json -> body.version
    android  Play internal track, read-only edit (needs the release keys)
    web      no public version; the last version this script shipped (state file)

Git history alone cannot say what is deployed, so it is not used for this.
When no source answers, the box is left empty and marked "?".

SSH cooldown. The VPS firewall rate-limits ssh connections, and server and web
each open many. When both are chosen, the web step waits until --ssh-cooldown
seconds (default 300) have passed since the server step ended. The agent step
runs inside that wait, since it does not use ssh.

Server health. deploy_central.py exits 3 when compose is up but its own health
poll failed. This script then polls /api/version itself for a longer time and
accepts the deploy when the expected server_version answers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from deploy_central import (
    DEPLOY_DIR,
    DOMAIN,
    EXIT_HEALTH_UNCONFIRMED,
    PROJECT_ROOT,
    dim,
    enable_ansi,
    fail,
    http_ssl_context,
    info,
    ok,
    require_min_python,
    step,
    warn,
)

MANIFEST_PATH = PROJECT_ROOT / ".release-please-manifest.json"
SERVER_VERSION_URL = f"https://{DOMAIN}/api/version"
AGENT_MANIFEST_URL = f"https://api.{DOMAIN}/api/v1/agent/manifest.json"
STATE_FILE_NAME = "deploy-state.json"
DEFAULT_SSH_COOLDOWN = 300
SERVER_VERIFY_TIMEOUT = 300


@dataclass
class Component:
    key: str
    label: str
    manifest_key: str
    script: str
    extra_args: list[str] = field(default_factory=list)
    pass_version: bool = False
    uses_ssh: bool = False

    def command(self, version: str) -> list[str]:
        cmd = [sys.executable, str(DEPLOY_DIR / self.script), *self.extra_args]
        if self.pass_version:
            cmd.append(version)
        return cmd


COMPONENTS: list[Component] = [
    Component("server", "central_server", "central_server", "deploy_central.py",
              ["--tls-mode", "CloudflareOrigin", "--force"], uses_ssh=True),
    Component("agent", "mobaide_agent", "mobaide_agent", "release_agent.py", pass_version=True),
    Component("web", "web app", "web", "deploy_web.py", uses_ssh=True),
    Component("android", "Android app", "android", "release_android.py", pass_version=True),
]
COMPONENT_KEYS = [c.key for c in COMPONENTS]


@dataclass
class Status:
    target: str
    live: str | None
    source: str

    @property
    def outdated(self) -> bool | None:
        if self.live is None:
            return None
        return _version_key(self.live) < _version_key(self.target)


def _version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("+")[0].split("."))
    except ValueError:
        return (0,)


# ------------------------------------------------------------ state file
def state_path() -> Path:
    from release_common import RELEASE_DIR_ENV, default_release_dir

    raw = os.environ.get(RELEASE_DIR_ENV)
    base = Path(raw).expanduser() if raw else default_release_dir()
    return base / STATE_FILE_NAME


def load_state() -> dict:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_shipped(key: str, version: str) -> None:
    path = state_path()
    state = load_state()
    state[key] = {"version": version, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        warn(f"could not write {path}: {e}")


# ------------------------------------------------------------ live versions
def fetch_json(url: str, timeout: float = 10) -> dict:
    """GET JSON; falls back to curl, whose trust store differs from Python's."""
    req = urllib.request.Request(url, headers={"User-Agent": "mobaide-deploy-all"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=http_ssl_context()) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        if shutil.which("curl") is None:
            raise
    r = subprocess.run(
        ["curl", "-fsS", "--max-time", str(int(timeout)), url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or f"curl exit {r.returncode}")
    return json.loads(r.stdout)


def live_server_version() -> str | None:
    return fetch_json(SERVER_VERSION_URL).get("server_version")


def live_agent_version() -> str | None:
    return fetch_json(AGENT_MANIFEST_URL).get("body", {}).get("version")


def live_android_version(target: str) -> str | None:
    """Newest versionName on the Play internal track, through a read-only edit."""
    import release_android as ra
    from release_common import http_json, read_secret, resolve_release_dir

    account = json.loads(read_secret(resolve_release_dir(None), ra.PLAY_ACCOUNT_FILE))
    auth = {"Authorization": f"Bearer {ra.play_access_token(account)}"}
    edit_id = http_json(f"{ra.PLAY_API}/edits", method="POST", data=b"{}",
                        headers={**auth, "Content-Type": "application/json"}, timeout=20)["id"]
    try:
        track = http_json(f"{ra.PLAY_API}/edits/{edit_id}/tracks/{ra.TRACK}", headers=auth, timeout=20)
    finally:
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"{ra.PLAY_API}/edits/{edit_id}", method="DELETE", headers=auth),
                timeout=20, context=http_ssl_context(),
            ).close()
        except (urllib.error.URLError, OSError):
            pass
    codes = [int(c) for rel in track.get("releases", []) for c in rel.get("versionCodes", [])]
    if not codes:
        return None
    newest = max(codes)
    if newest == ra.expected_version_code(target):
        return target
    return f"{newest // 10000}.{newest // 100 % 100}.{newest % 100}"


def detect(manifest: dict[str, str]) -> dict[str, Status]:
    state = load_state()
    out: dict[str, Status] = {}
    for c in COMPONENTS:
        target = manifest[c.manifest_key]
        live, source = None, "unknown"
        try:
            if c.key == "server":
                live, source = live_server_version(), "live /api/version"
            elif c.key == "agent":
                live, source = live_agent_version(), "live agent manifest"
            elif c.key == "android":
                live, source = live_android_version(target), "Play internal track"
        except Exception as e:  # noqa: BLE001 — any failure only means "unknown"
            dim(f"  {c.label}: live version unavailable ({type(e).__name__}: {e})")
            live = None
        if live is None and c.key in state:
            live, source = state[c.key].get("version"), f"last shipped {state[c.key].get('at', '?')[:10]}"
        out[c.key] = Status(target, live, source if live is not None else "unknown")
    return out


# ------------------------------------------------------------ selection
def render_menu(selected: set[str], statuses: dict[str, Status] | None) -> None:
    info("")
    info("Components to ship to production (iOS ships from Xcode):")
    for i, c in enumerate(COMPONENTS, 1):
        mark = "x" if c.key in selected else " "
        line = f"  {i}) [{mark}] {c.label:<15}"
        if statuses:
            s = statuses[c.key]
            live = s.live or "?"
            arrow = "=" if s.outdated is False else "->"
            line += f" {live:>8} {arrow} {s.target:<8}  ({s.source})"
        info(line)
    info("Type numbers to toggle (e.g. '1 3'), a = all, n = none, Enter = continue, q = quit.")


def choose_interactively(selected: set[str], statuses: dict[str, Status] | None) -> set[str] | None:
    while True:
        render_menu(selected, statuses)
        try:
            raw = input("> ").strip().lower()
        except EOFError:
            return None
        if raw == "":
            return selected
        if raw == "q":
            return None
        if raw == "a":
            selected = set(COMPONENT_KEYS)
            continue
        if raw == "n":
            selected = set()
            continue
        for tok in raw.replace(",", " ").split():
            if tok.isdigit() and 1 <= int(tok) <= len(COMPONENTS):
                selected ^= {COMPONENTS[int(tok) - 1].key}
            else:
                warn(f"ignored '{tok}'")


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ------------------------------------------------------------ git sanity
def git_out(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def check_checkout() -> list[str]:
    problems: list[str] = []
    branch = git_out("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        problems.append(f"HEAD is on '{branch or '?'}', not main")
    subprocess.run(["git", "fetch", "--quiet", "origin", "main"], cwd=PROJECT_ROOT,
                   capture_output=True)
    behind = git_out("rev-list", "--count", "HEAD..origin/main")
    if behind and behind != "0":
        problems.append(f"HEAD is {behind} commit(s) behind origin/main")
    if git_out("status", "--porcelain"):
        problems.append("the working tree has uncommitted changes (server deploys with --force)")
    return problems


# ------------------------------------------------------------ running
def wait_cooldown(seconds: float) -> None:
    if seconds <= 0:
        return
    step(f"SSH cooldown: waiting {int(seconds)}s before the next ssh deploy")
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        sys.stdout.write(f"\r  {int(left) // 60:02d}:{int(left) % 60:02d} left ")
        sys.stdout.flush()
        time.sleep(min(1.0, left))
    sys.stdout.write("\r" + " " * 20 + "\r")
    ok("cooldown done")


def verify_server(expected: str, timeout: int) -> bool:
    step(f"Verifying central_server {expected} at {SERVER_VERSION_URL} (up to {timeout}s)")
    deadline = time.monotonic() + timeout
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            live = live_server_version()
            if live == expected:
                ok(f"server_version {live} is live")
                return True
            last = f"server_version {live}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(10)
    fail(f"could not confirm server_version {expected}; last result: {last}")
    return False


def run_plan(
    plan: list[Component], manifest: dict[str, str], args: argparse.Namespace
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    last_ssh_end: float | None = None
    for i, c in enumerate(plan):
        version = manifest[c.manifest_key]
        if c.uses_ssh and last_ssh_end is not None:
            wait_cooldown(args.ssh_cooldown - (time.monotonic() - last_ssh_end))
        cmd = c.command(version)
        step(f"[{i + 1}/{len(plan)}] {c.label} {version}")
        dim("  $ " + " ".join(cmd))
        code = subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
        if c.uses_ssh:
            last_ssh_end = time.monotonic()

        if c.key == "server" and code == EXIT_HEALTH_UNCONFIRMED:
            warn("deploy_central.py: compose is up, but its health check did not pass")
            if verify_server(version, args.server_verify_timeout):
                code = 0
            elif not args.yes and confirm("Treat the server deploy as done and continue?"):
                code = 0

        if code != 0:
            fail(f"{c.label} failed (exit {code})")
            results.append((c.label, f"FAILED (exit {code})"))
            results += [(rest.label, "not run") for rest in plan[i + 1:]]
            return results
        record_shipped(c.key, version)
        results.append((c.label, f"shipped {version}"))
    return results


# ------------------------------------------------------------ main
def parse_only(raw: str) -> set[str]:
    keys = {k.strip().lower() for k in raw.split(",") if k.strip()}
    unknown = keys - set(COMPONENT_KEYS)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown component(s): {', '.join(sorted(unknown))}; choose from {', '.join(COMPONENT_KEYS)}"
        )
    return keys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deploy_all.py",
        description="Ship the chosen Mobaide components (server, agent, web, Android) to production.",
    )
    p.add_argument("--only", type=parse_only, metavar="LIST",
                   help=f"comma-separated components, skips the menu ({', '.join(COMPONENT_KEYS)})")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--dry-run", action="store_true", help="print the plan and commands, run nothing")
    p.add_argument("--no-detect", action="store_true", help="skip the live-version lookup")
    p.add_argument("--ssh-cooldown", type=int, default=DEFAULT_SSH_COOLDOWN, metavar="SEC",
                   help="minimum gap between the server and web ssh deploys (default: %(default)s)")
    p.add_argument("--server-verify-timeout", type=int, default=SERVER_VERIFY_TIMEOUT, metavar="SEC",
                   help="how long to poll /api/version when deploy_central's health check fails "
                        "(default: %(default)s)")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("deploy_all.py"):
        return 1
    args = build_parser().parse_args(argv)
    enable_ansi()
    if args.yes and args.only is None:
        fail("--yes needs --only (there is no menu to confirm)")
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    statuses: dict[str, Status] | None = None
    if not args.no_detect:
        step("Checking production versions")
        statuses = detect(manifest)

    if args.only is not None:
        selected = args.only
    else:
        selected = {k for k, s in (statuses or {}).items() if s.outdated}
        chosen = choose_interactively(selected, statuses)
        if chosen is None:
            info("Nothing shipped.")
            return 0
        selected = chosen

    plan = [c for c in COMPONENTS if c.key in selected]
    if not plan:
        info("Nothing selected.")
        return 0

    step("Plan")
    for c in plan:
        shown = [os.path.relpath(a, PROJECT_ROOT) if a.endswith(".py") else a
                 for a in c.command(manifest[c.manifest_key])[1:]]
        info(f"  {c.label:<15} {manifest[c.manifest_key]:<8} $ python {' '.join(shown)}")
    if sum(c.uses_ssh for c in plan) > 1:
        info(f"  (web waits until {args.ssh_cooldown}s have passed since the server deploy ended)")
    problems = check_checkout()
    for p in problems:
        warn(p)

    if args.dry_run:
        info("--dry-run: nothing was run.")
        return 0
    if not args.yes and not confirm("Ship to production?"):
        info("Nothing shipped.")
        return 0

    try:
        results = run_plan(plan, manifest, args)
    except KeyboardInterrupt:
        fail("interrupted")
        return 130

    step("Summary")
    for label, outcome in results:
        (ok if outcome.startswith("shipped") else warn)(f"{label:<15} {outcome}")
    return 0 if all(o.startswith("shipped") for _, o in results) else 1


if __name__ == "__main__":
    sys.exit(main())
