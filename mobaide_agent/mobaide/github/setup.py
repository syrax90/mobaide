"""Readiness check and setup commands for Auto Scan's GitHub issue source.

The status tells a client what stands between the user and a GitHub scan
(``gh`` missing, not signed in, no GitHub repository). The install and
sign-in commands run in a terminal session the user watches, because both
can ask questions (a ``sudo`` password, the sign-in method).
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from mobaide.core.cli_launcher import WINDOWS_NO_WINDOW
from mobaide.core.git_exec import GitCwdMissingError, GitUnavailableError, run_git
from mobaide.core.shell import login_shell_path

from .gh import GhBinaryNotFoundError, GhCli, GhError, gh_env

_DONE_NOTE = "GitHub CLI is installed. Go back to Auto Scan and check again."


@dataclass(frozen=True)
class SetupCommand:
    argv: list[str]
    # The command as the user would type it, for the wizard's hint.
    display: str


def _first_existing(paths: list[str]) -> str | None:
    for path in paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _brew() -> str | None:
    return shutil.which("brew") or _first_existing(
        [
            "/opt/homebrew/bin/brew",
            "/usr/local/bin/brew",
            "/home/linuxbrew/.linuxbrew/bin/brew",
            str(Path.home() / ".linuxbrew" / "bin" / "brew"),
        ]
    )


def _winget() -> str | None:
    found = shutil.which("winget")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return _first_existing([os.path.join(local, "Microsoft", "WindowsApps", "winget.exe")])
    return None


def _linux_package_command() -> str | None:
    sudo = "" if hasattr(os, "geteuid") and os.geteuid() == 0 else "sudo "
    if shutil.which("apt-get"):
        return f"{sudo}apt-get update && {sudo}apt-get install -y gh"
    if shutil.which("dnf"):
        return f"{sudo}dnf install -y gh"
    if shutil.which("pacman"):
        return f"{sudo}pacman -S --noconfirm github-cli"
    if shutil.which("zypper"):
        return f"{sudo}zypper --non-interactive install gh"
    return None


def install_command() -> SetupCommand | None:
    """The command that installs ``gh`` on this PC; ``None`` when none is known."""
    if sys.platform == "win32":
        winget = _winget()
        if winget is None:
            return None
        # Run directly, not through ``cmd /c``: the spawn quotes each argument
        # for the program itself, which cmd.exe would not unquote.
        return SetupCommand(
            argv=[
                winget, "install", "--id", "GitHub.cli", "-e", "--source", "winget",
                "--accept-source-agreements", "--accept-package-agreements",
            ],
            display="winget install --id GitHub.cli",
        )
    brew = _brew()
    if brew is not None:
        line = f"{shlex.quote(brew)} install gh"
        display = "brew install gh"
    elif sys.platform.startswith("linux"):
        line = _linux_package_command()
        if line is None:
            return None
        display = line
    else:
        return None
    script = f"{line} && echo && echo {shlex.quote(_DONE_NOTE)}"
    return SetupCommand(argv=[login_shell_path(), "-l", "-c", script], display=display)


def sign_in_command(gh_bin: str, host: str) -> SetupCommand:
    """``gh auth login`` for ``host``; it asks how to sign in (browser code or token)."""
    return SetupCommand(
        argv=[gh_bin, "auth", "login", "--hostname", host, "--git-protocol", "https"],
        display=f"gh auth login --hostname {host}",
    )


def sign_in_env(gh_bin: str) -> dict[str, str]:
    return gh_env(gh_bin, interactive=True)


def _remote_host(url: str) -> tuple[str, bool] | None:
    """Host of a git remote URL — ``https://h/o/r``, ``ssh://git@h/o/r``,
    ``git@h:o/r`` — and whether it goes through ssh."""
    url = url.strip()
    if "://" in url:
        parts = urlsplit(url)
        host = parts.hostname
        return (host.lower(), parts.scheme in ("ssh", "git+ssh")) if host else None
    if "@" in url and ":" in url.split("@", 1)[1]:
        host = url.split("@", 1)[1].split(":", 1)[0].lower()
        return (host, True) if host else None
    return None


def _ssh_hostname(alias: str) -> str | None:
    """The real host of an ssh alias (``Host github-work`` in ~/.ssh/config),
    as ``ssh -G`` prints it; ``None`` when ssh cannot tell."""
    try:
        completed = subprocess.run(
            ["ssh", "-G", "--", alias],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        key, _, value = line.partition(" ")
        if key == "hostname" and value.strip():
            return value.strip().lower()
    return None


def _github_name(host: str) -> str:
    # gh treats ``ssh.github.com`` (ssh over port 443) and the other
    # ``*.github.com`` names as github.com.
    return "github.com" if host.endswith(".github.com") else host


def github_host(hosts: list[str]) -> str | None:
    """The host to check and sign in to: the first GitHub-looking remote host,
    else the first remote host; ``None`` without remotes."""
    return next((h for h in hosts if "github" in h), hosts[0] if hosts else None)


async def remote_hosts(path: str) -> list[str]:
    """Hosts of the folder's git remotes, ``origin`` first, with ssh aliases
    resolved the way ``gh`` resolves them."""
    try:
        result = await run_git(["remote", "-v"], cwd=path, timeout=10.0, max_bytes=65536)
    except (GitCwdMissingError, GitUnavailableError):
        return []
    if result.rc != 0:
        return []
    by_remote: dict[str, str] = {}
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in by_remote:
            found = _remote_host(parts[1])
            if found is None:
                continue
            host, via_ssh = found
            if via_ssh:
                host = await asyncio.to_thread(_ssh_hostname, host) or host
            by_remote[parts[0]] = _github_name(host)
    ordered = ([by_remote.pop("origin")] if "origin" in by_remote else []) + list(by_remote.values())
    return list(dict.fromkeys(ordered))


async def github_status(gh: GhCli, workspace_path: str) -> dict[str, object]:
    """What a GitHub scan of ``workspace_path`` still needs, as the wire dict.

    ``problem`` is the first blocking reason in plain words, ``None`` when a
    scan can start.
    """
    command = install_command()
    status: dict[str, object] = {
        "installed": False,
        "version": None,
        "signed_in": False,
        "account": None,
        "host": None,
        "repo": None,
        "repo_url": None,
        "problem": None,
        "can_install": command is not None,
        "install_command": command.display if command is not None else None,
    }
    try:
        gh.resolve_binary()
    except GhBinaryNotFoundError:
        status["problem"] = "GitHub CLI (gh) is not installed on the PC."
        return status
    status["installed"] = True
    status["version"] = await gh.version()

    # ``host`` stays ``None`` without a git repository or a remote. Any remote
    # sets it, GitHub or not; clients offer sign-in only for a GitHub host.
    if not os.path.exists(os.path.join(workspace_path, ".git")):
        status["problem"] = "This workspace is not a git repository."
        return status

    host = github_host(await remote_hosts(workspace_path))
    if host is None:
        status["problem"] = "This workspace's repository has no remote."
        return status
    status["host"] = host
    auth = await gh.auth_status(host)
    status["signed_in"] = auth.signed_in
    status["account"] = auth.account
    if not auth.signed_in:
        if "github" not in host:
            status["problem"] = f"The repository's remote is on {host}, not on GitHub."
        else:
            status["problem"] = auth.detail or f"GitHub CLI is not signed in to {host}."
        return status
    try:
        repo = await gh.repo_of(workspace_path)
    except GhError as exc:
        status["problem"] = str(exc)
        return status
    if repo.host != host:
        # gh chose another remote (it ranks ``upstream`` first): the scan
        # reads that repository, so its host is the one that must be signed in.
        status["host"] = repo.host
        auth = await gh.auth_status(repo.host)
        status["signed_in"] = auth.signed_in
        status["account"] = auth.account
        if not auth.signed_in:
            status["problem"] = auth.detail or f"GitHub CLI is not signed in to {repo.host}."
            return status
    status["repo"] = repo.name_with_owner
    status["repo_url"] = repo.url
    return status
