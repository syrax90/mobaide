"""The GitHub CLI wrapper and the readiness check behind Auto Scan's GitHub
issue source (``mobaide.github``).

``gh`` never runs: ``subprocess.run`` is swapped for a recorder, or ``GhCli``
for a hand-written fake. ``git`` runs for real on throwaway repositories, with
the developer's global config shut out so ``insteadOf`` rewrites cannot change
a remote URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mobaide.agent.autoscan.models import GitHubStatusResponse
from mobaide.github import gh as gh_module
from mobaide.github import setup
from mobaide.github.gh import (
    GhAuth,
    GhBinaryNotFoundError,
    GhCli,
    GhError,
    GitHubRepo,
)

from .git_repo_support import init_repo, requires_git, run_git_sync

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX file modes")


_REAL_SSH_HOSTNAME = setup._ssh_hostname


@pytest.fixture(autouse=True)
def _no_real_ssh(monkeypatch):
    """ssh remotes resolve aliases through ``ssh -G``; keep the host's ssh config out."""
    monkeypatch.setattr(setup, "_ssh_hostname", lambda alias: None)


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


class _PinnedGh(GhCli):
    def resolve_binary(self) -> str:
        return "gh"


class _RecordingRun:
    """Stands in for ``subprocess.run``: records each call and answers ``gh``
    subcommands from a table keyed by the first two arguments."""

    def __init__(self, answers: dict[tuple[str, str], object]) -> None:
        self._answers = answers
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        answer = self._answers.get((argv[1], argv[2]), "")
        stdout = answer if isinstance(answer, str) else json.dumps(answer)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout.encode(), stderr=b"")

    def argv_of(self, subcommand: str) -> list[str]:
        return next(
            call["argv"] for call in self.calls
            if " ".join(call["argv"][1:3]) == subcommand  # type: ignore[index]
        )


class _ScriptedRun:
    """Stands in for ``subprocess.run``: answers each call with the next
    ``(returncode, stdout, stderr)`` and records the ``gh`` arguments."""

    def __init__(self, answers: list[tuple[int, str, str]]) -> None:
        self._answers = list(answers)
        self.args: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.args.append(list(argv[1:]))
        rc, out, err = self._answers.pop(0)
        return subprocess.CompletedProcess(argv, rc, stdout=out.encode(), stderr=err.encode())


class TestGhCliCalls:
    async def test_user_values_travel_as_single_flag_elements(self, monkeypatch, tmp_path):
        run = _RecordingRun(
            {
                ("repo", "view"): {
                    "nameWithOwner": "acme/app",
                    "url": "https://ghe.example.com/acme/app",
                },
                ("issue", "list"): [
                    {
                        "number": 7,
                        "title": "Crash",
                        "body": "steps",
                        "url": "https://ghe.example.com/acme/app/issues/7",
                        "labels": [{"name": "--web"}, {"name": "bug"}],
                    },
                    {"number": "8", "title": "not a number"},
                    "garbage",
                ],
            }
        )
        monkeypatch.setattr(gh_module.subprocess, "run", run)
        gh = _PinnedGh()

        repo = await gh.repo_of(str(tmp_path))
        issues = await gh.list_issues(repo, label="--web", assignee="--me")
        await gh.edit_labels(repo, 7, add=("-taken",), remove=("--web",))

        # An Enterprise repository needs its host in every ``--repo``.
        assert repo.selector == "ghe.example.com/acme/app"
        listed = run.argv_of("issue list")
        assert listed[listed.index("--repo") + 1] == "ghe.example.com/acme/app"
        # A label or login that starts with "-" can never become another flag.
        assert "--label=--web" in listed
        assert "--assignee=--me" in listed
        assert "--web" not in listed
        edited = run.argv_of("issue edit")
        assert "--add-label=-taken" in edited
        assert "--remove-label=--web" in edited
        # Prompts stay off and stdin is closed: a missing sign-in fails fast
        # instead of waiting on a question nobody sees.
        for call in run.calls:
            assert call["env"]["GH_PROMPT_DISABLED"] == "1"  # type: ignore[index]
            assert call["stdin"] is subprocess.DEVNULL
        assert [(i.number, i.labels) for i in issues] == [(7, ("--web", "bug"))]

    @pytest.mark.parametrize(
        ("answers", "expected", "calls"),
        [
            pytest.param(
                [(0, "", "✓ Logged in to github.com account octocat (keyring)\n- Active account: true")],
                GhAuth(signed_in=True, account="octocat"),
                [["auth", "status", "--active", "--hostname", "github.com"]],
                id="active-account-only",
            ),
            pytest.param(
                [
                    (1, "", "unknown flag: --active\n\nUsage:  gh auth status [flags]"),
                    (0, "", "✓ Logged in to github.com as octocat (oauth_token)"),
                ],
                GhAuth(signed_in=True, account="octocat"),
                [
                    ["auth", "status", "--active", "--hostname", "github.com"],
                    ["auth", "status", "--hostname", "github.com"],
                ],
                id="gh-before-2.40-asked-again-without-the-flag",
            ),
            pytest.param(
                [(1, "", "You are not logged into any GitHub hosts. To log in, run: gh auth login")],
                GhAuth(signed_in=False, detail="GitHub CLI is not signed in to github.com."),
                [["auth", "status", "--active", "--hostname", "github.com"]],
                id="signed-out-is-not-asked-again",
            ),
        ],
    )
    async def test_auth_status_checks_the_active_account(
        self, monkeypatch, answers, expected, calls,
    ):
        run = _ScriptedRun(answers)
        monkeypatch.setattr(gh_module.subprocess, "run", run)

        auth = await _PinnedGh().auth_status("github.com")

        assert auth == expected
        assert run.args == calls

    async def test_labels_with_a_comma_are_not_offered(self, monkeypatch):
        # gh splits a ``--label`` value on commas, so a scan could never pick
        # up an issue by such a label.
        rows = [
            {"name": "ai-ready", "color": "0E8A16"},
            {"name": "needs triage, maybe", "color": "D93F0B"},
            {"name": "bug", "color": "D73A4A"},
        ]
        monkeypatch.setattr(gh_module.subprocess, "run", _ScriptedRun([(0, json.dumps(rows), "")]))
        repo = GitHubRepo(host="github.com", name_with_owner="acme/app",
                          url="https://github.com/acme/app")

        labels = await _PinnedGh().list_labels(repo)

        assert labels == [
            {"name": "ai-ready", "color": "0E8A16"},
            {"name": "bug", "color": "D73A4A"},
        ]

    async def test_gh_answers_while_the_default_executor_is_full(self, monkeypatch):
        """Every open terminal session holds a blocked read there."""
        monkeypatch.setattr(
            gh_module.subprocess, "run", _ScriptedRun([(0, "gh version 2.83.0 (2025-11-04)\n", "")]),
        )
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        held = threading.Event()
        loop.run_in_executor(None, held.wait)
        try:
            version = await asyncio.wait_for(_PinnedGh().version(), 5)
        finally:
            held.set()

        assert version == "2.83.0"

    @posix_only
    def test_binary_found_right_after_an_install_without_a_restart(
        self, monkeypatch, tmp_path,
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        target = tmp_path / "bin" / "gh"
        monkeypatch.setenv("PATH", str(empty))
        monkeypatch.setenv("MOBAIDE_GH_BIN", str(target))
        monkeypatch.setattr(gh_module, "_fallback_candidates", lambda: [])
        gh = GhCli()

        with pytest.raises(GhBinaryNotFoundError):
            gh.resolve_binary()
        target.parent.mkdir()
        target.write_text("#!/bin/sh\n")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)

        assert gh.resolve_binary() == str(target)


@pytest.mark.parametrize(
    ("platform", "brew", "tools", "euid", "argv_head", "line", "display"),
    [
        pytest.param(
            "linux", None, {"apt-get"}, 1000, ["/bin/zsh", "-l", "-c"],
            "sudo apt-get update && sudo apt-get install -y gh",
            "sudo apt-get update && sudo apt-get install -y gh",
            id="linux-apt-needs-sudo",
        ),
        pytest.param(
            "linux", None, {"dnf", "pacman"}, 0, ["/bin/zsh", "-l", "-c"],
            "dnf install -y gh", "dnf install -y gh",
            id="linux-root-without-sudo",
        ),
        pytest.param(
            "linux", "/home/linuxbrew/.linuxbrew/bin/brew", {"apt-get"}, 1000,
            ["/bin/zsh", "-l", "-c"],
            "/home/linuxbrew/.linuxbrew/bin/brew install gh", "brew install gh",
            id="homebrew-wins-over-the-package-manager",
        ),
        pytest.param("linux", None, set(), 1000, None, None, None, id="linux-unknown"),
        pytest.param("darwin", None, set(), 501, None, None, None, id="mac-without-homebrew"),
    ],
)
def test_install_command(monkeypatch, platform, brew, tools, euid, argv_head, line, display):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(setup, "_brew", lambda: brew)
    monkeypatch.setattr(setup, "_winget", lambda: "C:/winget.exe" if "winget" in tools else None)
    monkeypatch.setattr(setup.shutil, "which", lambda name: f"/usr/bin/{name}" if name in tools else None)
    monkeypatch.setattr(setup.os, "geteuid", lambda: euid, raising=False)
    monkeypatch.setattr(setup, "login_shell_path", lambda: "/bin/zsh")

    command = setup.install_command()

    if argv_head is None:
        assert command is None
        return
    assert command is not None
    assert command.display == display
    assert command.argv[: len(argv_head)] == argv_head
    assert len(command.argv) == len(argv_head) + 1
    assert command.argv[-1].startswith(f"{line} && ")


def test_windows_install_runs_winget_itself_not_through_cmd(monkeypatch):
    winget = r"C:\Program Files\WindowsApps\winget.exe"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(setup, "_winget", lambda: winget)

    command = setup.install_command()

    assert command is not None
    # One element per argument: the spawn quotes each one for winget, and no
    # cmd.exe is there to misread those quotes.
    assert command.argv == [
        winget, "install", "--id", "GitHub.cli", "-e", "--source", "winget",
        "--accept-source-agreements", "--accept-package-agreements",
    ]
    assert command.display == "winget install --id GitHub.cli"


@requires_git
async def test_remote_hosts_put_origin_first_and_read_every_url_form(tmp_path):
    repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
    run_git_sync(repo, "remote", "add", "origin", "git@GitLab.com:acme/app.git")
    run_git_sync(repo, "remote", "add", "upstream", "https://github.com/acme/app.git")
    run_git_sync(repo, "remote", "add", "mirror", "ssh://git@ghe.example.com:2222/acme/app.git")
    run_git_sync(repo, "remote", "add", "dup", "https://github.com/someone/fork.git")

    assert await setup.remote_hosts(repo) == ["gitlab.com", "github.com", "ghe.example.com"]


@requires_git
async def test_remote_hosts_resolve_ssh_aliases_the_way_gh_does(monkeypatch, tmp_path):
    repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
    run_git_sync(repo, "remote", "add", "origin", "git@github-work:acme/app.git")
    run_git_sync(repo, "remote", "add", "upstream", "ssh://git@ssh.github.com:443/acme/app.git")
    run_git_sync(repo, "remote", "add", "mirror", "https://ghe.example.com/acme/app.git")
    asked: list[str] = []

    def ssh_hostname(alias: str) -> str | None:
        asked.append(alias)
        # ``Host github-work`` in ~/.ssh/config; ssh.github.com is a real host.
        return {"github-work": "github.com"}.get(alias)

    monkeypatch.setattr(setup, "_ssh_hostname", ssh_hostname)

    hosts = await setup.remote_hosts(repo)

    # Both ssh remotes are github.com to gh: the alias resolves to it, and
    # ssh.github.com (ssh over port 443) is one of its names.
    assert hosts == ["github.com", "ghe.example.com"]
    # Only ssh remotes go through ssh's config.
    assert sorted(asked) == ["github-work", "ssh.github.com"]


def _ssh_raises(exc: BaseException):
    def run(argv, **kwargs):
        raise exc
    return run


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        pytest.param(
            _ScriptedRun([(0, "user git\nhostname GitHub.com\nport 22\n", "")]), "github.com",
            id="alias-resolved",
        ),
        pytest.param(
            _ScriptedRun([(255, "", "Bad configuration option: foo")]), None,
            id="broken-ssh-config",
        ),
        pytest.param(_ssh_raises(subprocess.TimeoutExpired("ssh", 5)), None, id="ssh-hangs"),
        pytest.param(
            _ssh_raises(FileNotFoundError(2, "No such file or directory", "ssh")), None,
            id="ssh-missing",
        ),
    ],
)
def test_ssh_hostname_reads_the_real_host_or_gives_none(monkeypatch, run, expected):
    monkeypatch.setattr(setup.subprocess, "run", run)
    # The host part of ``git@-oProxyCommand=calc:o/r``: a remote URL is
    # repository data and must never turn into an ssh option.
    alias = "-oproxycommand=calc"

    assert _REAL_SSH_HOSTNAME(alias) == expected
    if isinstance(run, _ScriptedRun):
        assert run.args == [["-G", "--", alias]]


class _FakeGh(GhCli):
    """``gh`` with a fixed answer per question; records the hosts asked about."""

    def __init__(
        self, *, installed: bool = True, signed_in: bool = True,
        repo_error: str | None = None,
    ) -> None:
        super().__init__()
        self._installed = installed
        self._signed_in = signed_in
        self._repo_error = repo_error
        self.auth_hosts: list[str] = []

    def resolve_binary(self) -> str:
        if not self._installed:
            raise GhBinaryNotFoundError(searched_paths=[])
        return "gh"

    async def version(self) -> str | None:
        return "2.83.0"

    async def auth_status(self, host: str) -> GhAuth:
        self.auth_hosts.append(host)
        if self._signed_in:
            return GhAuth(signed_in=True, account="octocat")
        return GhAuth(signed_in=False, detail=f"not signed in to {host}")

    async def repo_of(self, path: str) -> GitHubRepo:
        if self._repo_error is not None:
            raise GhError(self._repo_error)
        return GitHubRepo(
            host="github.com", name_with_owner="acme/app", url="https://github.com/acme/app",
        )


def _plain_folder(root) -> str:
    path = root / "plain"
    path.mkdir()
    return str(path)


def _repo_with(*remotes: tuple[str, str]):
    def make(root) -> str:
        repo = init_repo(str(root / "repo"), initial_commit=False)
        for name, url in remotes:
            run_git_sync(repo, "remote", "add", name, url)
        return repo
    return make


_GITHUB_ORIGIN = ("origin", "https://github.com/acme/app.git")


@requires_git
@pytest.mark.parametrize(
    ("make_folder", "fake", "expected"),
    [
        pytest.param(
            _repo_with(_GITHUB_ORIGIN), _FakeGh(installed=False),
            {"installed": False, "signed_in": False, "repo": None, "blocked": True},
            id="gh-missing",
        ),
        pytest.param(
            _repo_with(("origin", "git@gitlab.com:acme/app.git")), _FakeGh(signed_in=False),
            {"host": "gitlab.com", "signed_in": False, "github_host": False, "repo": None,
             "blocked": True},
            id="remote-not-on-github",
        ),
        pytest.param(
            _repo_with(_GITHUB_ORIGIN), _FakeGh(signed_in=False),
            {"host": "github.com", "signed_in": False, "github_host": True, "repo": None,
             "blocked": True},
            id="not-signed-in",
        ),
        pytest.param(
            _repo_with(("origin", "git@ghe.example.com:acme/app.git")), _FakeGh(signed_in=False),
            {"host": "ghe.example.com", "signed_in": False, "github_host": True, "repo": None,
             "blocked": True},
            id="enterprise-host-without-github-in-its-name",
        ),
        pytest.param(
            _repo_with(_GITHUB_ORIGIN), _FakeGh(repo_error="no GitHub repository here"),
            {"signed_in": True, "repo": None, "problem": "no GitHub repository here"},
            id="repo-not-resolved",
        ),
        pytest.param(
            _repo_with(("origin", "git@gitlab.com:acme/app.git"), ("upstream", _GITHUB_ORIGIN[1])),
            _FakeGh(),
            {
                "installed": True, "version": "2.83.0", "signed_in": True,
                "account": "octocat", "host": "github.com", "repo": "acme/app",
                "repo_url": "https://github.com/acme/app", "problem": None,
            },
            id="ready-on-the-github-remote",
        ),
    ],
)
async def test_github_status_names_the_first_blocker(
    monkeypatch, tmp_path, make_folder, fake, expected,
):
    monkeypatch.setattr(setup, "install_command", lambda: None)

    async def answers_as_github(host: str) -> bool:
        return host == "ghe.example.com"

    monkeypatch.setattr(setup, "answers_as_github", answers_as_github)
    folder = make_folder(tmp_path)

    status = await setup.github_status(fake, folder)

    expected = dict(expected)
    # Every key the wire model declares, and nothing else.
    assert set(status) == set(GitHubStatusResponse.model_fields)
    blocked = expected.pop("blocked", None)
    if blocked:
        assert isinstance(status["problem"], str) and status["problem"]
    for key, value in expected.items():
        assert status[key] == value, key
    if status["host"] is not None and fake.auth_hosts:
        assert fake.auth_hosts == [status["host"]]


class _OtherRemoteGh(_FakeGh):
    """``gh repo view`` picks the repository on ``repo_host`` (gh ranks
    ``upstream`` over ``origin``); signed in only on ``signed_in_hosts``."""

    def __init__(self, repo_host: str, signed_in_hosts: set[str]) -> None:
        super().__init__()
        self._repo_host = repo_host
        self._signed_in_hosts = signed_in_hosts

    async def auth_status(self, host: str) -> GhAuth:
        self.auth_hosts.append(host)
        if host in self._signed_in_hosts:
            return GhAuth(signed_in=True, account="octocat")
        return GhAuth(signed_in=False, detail=f"not signed in to {host}")

    async def repo_of(self, path: str) -> GitHubRepo:
        return GitHubRepo(
            host=self._repo_host, name_with_owner="acme/app",
            url=f"https://{self._repo_host}/acme/app",
        )


@requires_git
@pytest.mark.parametrize(
    ("signed_in_hosts", "expected"),
    [
        pytest.param(
            {"github.com"},
            {"signed_in": False, "account": None, "repo": None, "repo_url": None,
             "problem": "not signed in to github.acme.dev"},
            id="signed-in-only-on-the-origin-host",
        ),
        pytest.param(
            {"github.com", "github.acme.dev"},
            {"signed_in": True, "account": "octocat", "repo": "acme/app",
             "repo_url": "https://github.acme.dev/acme/app", "problem": None},
            id="signed-in-on-the-repository-host",
        ),
    ],
)
async def test_github_status_checks_the_host_of_the_repository_gh_scans(
    monkeypatch, tmp_path, signed_in_hosts, expected,
):
    monkeypatch.setattr(setup, "install_command", lambda: None)
    folder = _repo_with(
        _GITHUB_ORIGIN, ("upstream", "https://github.acme.dev/acme/app.git"),
    )(tmp_path)
    fake = _OtherRemoteGh("github.acme.dev", signed_in_hosts)

    status = await setup.github_status(fake, folder)

    # The scan reads the repository gh chose, so a sign-in on the origin's
    # host alone must not report the workspace as ready.
    assert status["host"] == "github.acme.dev"
    assert fake.auth_hosts == ["github.com", "github.acme.dev"]
    for key, value in expected.items():
        assert status[key] == value, key


@requires_git
@pytest.mark.parametrize(
    "make_folder",
    [
        pytest.param(_plain_folder, id="not-a-git-folder"),
        pytest.param(_repo_with(), id="no-remote"),
    ],
)
async def test_github_status_names_no_host_where_signing_in_cannot_help(
    monkeypatch, tmp_path, make_folder,
):
    monkeypatch.setattr(setup, "install_command", lambda: None)
    fake = _FakeGh(signed_in=False)

    status = await setup.github_status(fake, make_folder(tmp_path))

    # Clients offer "Sign in to GitHub" only for a GitHub host; signing in
    # cannot turn a plain folder or a remote-less repository into one.
    assert status["host"] is None
    assert (status["installed"], status["signed_in"]) == (True, False)
    assert isinstance(status["problem"], str) and status["problem"]
    assert fake.auth_hosts == []
