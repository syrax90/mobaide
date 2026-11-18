"""GitHub CLI (``gh``) access for Auto Scan's GitHub issue source.

Every call is a one-shot ``gh`` subprocess with prompts off, so a missing
sign-in fails fast instead of waiting on a prompt nobody sees. Calls run in
a worker thread (``subprocess.run``) for the same reason ``git`` calls do:
``asyncio.create_subprocess_exec`` is not available under the selector event
loop the agent installs on Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mobaide.core.cli_launcher import (
    WINDOWS_NO_WINDOW,
    CliBinaryNotFoundError,
    prepend_path_dir,
    resolve_cli_binary,
)

_GH_BIN_ENV_VAR = "MOBAIDE_GH_BIN"
# Not the default executor: running PTY sessions keep threads there blocked
# in reads, and a slow GitHub must not hold the rest. It also caps how many
# ``gh`` calls run at once.
_GH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mobaide-gh")
_TIMEOUT = 20.0
_MAX_ERROR = 300
_ISSUE_LIMIT = 50
_LABEL_LIMIT = 200
_ACCOUNT_RE = re.compile(r"(?:account|as)\s+([A-Za-z0-9-]+)")


class GhBinaryNotFoundError(CliBinaryNotFoundError):
    """Raised when the GitHub CLI cannot be located on the host."""

    cli_name = "gh"
    _INSTALL_HINT = (
        "GitHub CLI (gh) not found. Install it from https://cli.github.com, "
        "or set MOBAIDE_GH_BIN to its absolute path in the agent's env file."
    )


class GhError(RuntimeError):
    """A ``gh`` call failed; the message is short and safe to show."""


@dataclass(frozen=True)
class GitHubRepo:
    host: str
    name_with_owner: str
    url: str

    @property
    def selector(self) -> str:
        """The ``--repo`` value: ``[HOST/]OWNER/REPO``."""
        if self.host == "github.com":
            return self.name_with_owner
        return f"{self.host}/{self.name_with_owner}"


@dataclass(frozen=True)
class GhIssue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class GhAuth:
    signed_in: bool
    account: str | None = None
    detail: str | None = None


def _fallback_candidates() -> list[str]:
    if sys.platform == "win32":
        candidates: list[str] = []
        for var in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                candidates.append(os.path.join(base, "GitHub CLI", "gh.exe"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(os.path.join(local, "Microsoft", "WinGet", "Links", "gh.exe"))
        return candidates
    return [
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "/home/linuxbrew/.linuxbrew/bin/gh",
        str(Path.home() / ".linuxbrew" / "bin" / "gh"),
        "/usr/bin/gh",
        "/snap/bin/gh",
        str(Path.home() / ".local" / "bin" / "gh"),
    ]


def resolve_gh_binary(override: str | None = None) -> str:
    """Locate ``gh``. Not cached: an install made from the app is found at once."""
    which_names = ("gh", "gh.exe") if sys.platform == "win32" else ("gh",)
    return resolve_cli_binary(
        override=override,
        env_var=_GH_BIN_ENV_VAR,
        which_names=which_names,
        fallbacks=_fallback_candidates(),
        error_cls=GhBinaryNotFoundError,
    )


def gh_env(gh_bin: str, *, interactive: bool = False) -> dict[str, str]:
    """The environment for ``gh``; prompts stay off unless ``interactive``."""
    env = os.environ.copy()
    prepend_path_dir(env, os.path.dirname(gh_bin))
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    if interactive:
        env.pop("GH_PROMPT_DISABLED", None)
    else:
        env["GH_PROMPT_DISABLED"] = "1"
        env["NO_COLOR"] = "1"
        env.pop("GH_PAGER", None)
        env["PAGER"] = "cat"
    return env


def _short_error(stderr: str, stdout: str = "") -> str:
    text = (stderr or stdout or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    message = lines[0] if lines else "gh failed"
    # gh prefixes its errors with an "X " or "! " status mark.
    message = re.sub(r"^[X!]\s+", "", message) or message
    return message[:_MAX_ERROR]


class GhCli:
    """Run ``gh`` commands for one host process.

    ``override`` is the configured binary path; ``None`` searches the
    environment variable, PATH and the usual install folders.
    """

    def __init__(self, override: str | None = None) -> None:
        self._override = override

    def resolve_binary(self) -> str:
        return resolve_gh_binary(self._override)

    async def _run(
        self, args: list[str], *, cwd: str | None = None, timeout: float = _TIMEOUT,
    ) -> tuple[int, str, str]:
        gh_bin = self.resolve_binary()
        env = gh_env(gh_bin)

        def call() -> tuple[int, str, str]:
            try:
                completed = subprocess.run(
                    [gh_bin, *args],
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout,
                    creationflags=WINDOWS_NO_WINDOW,
                )
            except subprocess.TimeoutExpired:
                return 124, "", "gh did not answer in time"
            except OSError as exc:
                return 127, "", exc.strerror or str(exc)
            return (
                completed.returncode,
                completed.stdout.decode("utf-8", errors="replace"),
                completed.stderr.decode("utf-8", errors="replace"),
            )

        return await asyncio.get_running_loop().run_in_executor(_GH_POOL, call)

    async def _run_ok(self, args: list[str], *, cwd: str | None = None) -> str:
        rc, out, err = await self._run(args, cwd=cwd)
        if rc != 0:
            raise GhError(_short_error(err, out))
        return out

    async def version(self) -> str | None:
        rc, out, _ = await self._run(["--version"], timeout=10.0)
        if rc != 0:
            return None
        match = re.search(r"gh version (\S+)", out)
        return match.group(1) if match else None

    async def auth_status(self, host: str) -> GhAuth:
        # ``--active``: without it a stale second account on the host makes
        # the whole check fail. gh before 2.40 has no such flag.
        rc, out, err = await self._run(["auth", "status", "--active", "--hostname", host])
        if rc != 0 and "unknown flag" in err:
            rc, out, err = await self._run(["auth", "status", "--hostname", host])
        text = f"{out}\n{err}"
        if rc != 0:
            detail = _short_error(err, out)
            if "not logged" in text.lower():
                detail = f"GitHub CLI is not signed in to {host}."
            return GhAuth(signed_in=False, detail=detail)
        match = _ACCOUNT_RE.search(text)
        return GhAuth(signed_in=True, account=match.group(1) if match else None)

    async def repo_of(self, path: str) -> GitHubRepo:
        """The GitHub repository of the git folder ``path``.

        ``gh repo view`` resolves it from the git remotes and honours the
        folder's ``gh repo set-default`` choice.
        """
        out = await self._run_ok(["repo", "view", "--json", "nameWithOwner,url"], cwd=path)
        try:
            data = json.loads(out)
            name = str(data["nameWithOwner"])
            url = str(data["url"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GhError("gh returned an unexpected repository answer") from exc
        host = re.sub(r"^https?://", "", url).split("/", 1)[0] or "github.com"
        return GitHubRepo(host=host, name_with_owner=name, url=url)

    async def list_issues(
        self, repo: GitHubRepo, *, label: str, assignee: str | None,
    ) -> list[GhIssue]:
        # ``--flag=value`` forms: a user-chosen value that starts with ``-``
        # can never be read as another flag.
        args = [
            "issue", "list", "--repo", repo.selector, "--state", "open",
            f"--label={label}", "--limit", str(_ISSUE_LIMIT),
            "--json", "number,title,body,url,labels",
        ]
        if assignee:
            args.append(f"--assignee={assignee}")
        out = await self._run_ok(args)
        try:
            rows = json.loads(out)
        except ValueError as exc:
            raise GhError("gh returned an unexpected issue list") from exc
        issues: list[GhIssue] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("number"), int):
                continue
            labels = tuple(
                str(item.get("name"))
                for item in row.get("labels") or []
                if isinstance(item, dict) and item.get("name")
            )
            issues.append(
                GhIssue(
                    number=row["number"],
                    title=str(row.get("title") or ""),
                    body=str(row.get("body") or ""),
                    url=str(row.get("url") or ""),
                    labels=labels,
                )
            )
        return issues

    async def edit_labels(
        self, repo: GitHubRepo, number: int, *,
        add: tuple[str, ...] = (), remove: tuple[str, ...] = (),
    ) -> None:
        args = ["issue", "edit", str(number), "--repo", repo.selector]
        args += [f"--add-label={label}" for label in add]
        args += [f"--remove-label={label}" for label in remove]
        await self._run_ok(args)

    async def issue_state(self, repo: GitHubRepo, number: int) -> str | None:
        """``OPEN`` or ``CLOSED``; ``None`` when GitHub gave no state."""
        out = await self._run_ok(
            ["issue", "view", str(number), "--repo", repo.selector, "--json", "state"]
        )
        try:
            state = json.loads(out).get("state")
        except (ValueError, AttributeError):
            return None
        return state if isinstance(state, str) else None

    async def list_labels(self, repo: GitHubRepo) -> list[dict[str, str]]:
        out = await self._run_ok(
            [
                "label", "list", "--repo", repo.selector,
                "--limit", str(_LABEL_LIMIT), "--json", "name,color",
            ]
        )
        try:
            rows = json.loads(out)
        except ValueError as exc:
            raise GhError("gh returned an unexpected label list") from exc
        if not isinstance(rows, list):
            return []
        return [
            {"name": str(row["name"]), "color": str(row.get("color") or "")}
            for row in rows
            if isinstance(row, dict) and row.get("name")
        ]
