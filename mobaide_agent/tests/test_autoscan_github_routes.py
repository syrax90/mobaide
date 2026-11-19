"""Auto Scan's GitHub routes and start errors over both transports (the FastAPI
router and the relay ``_dispatch_proxy``), ``capabilities.autoscan`` on both
``/api/version`` builders, the setup terminal's command, and the Auto Scan
golden fixtures through the agent's own wire models.

``gh`` is absent from the PC in the transport tests: the lookup sees an empty
PATH and no install folders, so no host's own ``gh`` changes an answer.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mobaide.agent.autoscan import service
from mobaide.agent.autoscan.models import (
    AUTOSCAN_CAPABILITIES,
    AutoScanInstanceDto,
    StartScanRequest,
)
from mobaide.agent.workspaces.models import WorkspaceReadModel
from mobaide.core import SessionKind, WorkspaceRecord, WorkspacesStore
from mobaide.github import gh as gh_module
from mobaide.github.gh import GhAuth, GhCli, GitHubRepo
from mobaide.github.setup import SetupCommand
from mobaide.relay.client import RelayClient

from .git_repo_support import init_repo, requires_git, run_git_sync


@pytest.fixture(autouse=True)
def _no_real_ssh(monkeypatch):
    """ssh remotes resolve aliases through ``ssh -G``; keep the host's ssh config out."""
    monkeypatch.setattr("mobaide.github.setup._ssh_hostname", lambda alias: None)

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "protocol_fixtures" / "dto" / "agent"


def _case(type_name: str, case_name: str) -> dict:
    doc = json.loads((_FIXTURES / f"{type_name}.json").read_text(encoding="utf-8"))
    assert doc["type"] == type_name
    return next(case for case in doc["cases"] if case["name"] == case_name)


@pytest.fixture
def no_gh(monkeypatch, tmp_path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("MOBAIDE_GH_BIN", raising=False)
    monkeypatch.setattr(gh_module, "_fallback_candidates", lambda: [])
    monkeypatch.setattr(service, "install_command", lambda: None)


@pytest.fixture
def workspace(app, tmp_path) -> str:
    folder = tmp_path / "alpha"
    folder.mkdir()
    app.state.workspaces_store.create(WorkspaceRecord(name="alpha", path=str(folder)))
    return "alpha"


@pytest.fixture
def relay(app, relay_config, mock_session_manager, mock_event_bus, mock_broadcaster,
          mock_app_state):
    mock_app_state.workspaces_store = app.state.workspaces_store
    mock_app_state.autoscan_manager = app.state.autoscan_manager
    mock_app_state.cli_launchers = {}
    mock_app_state.cli_models_warmup = None
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


class TestTransports:
    @pytest.mark.parametrize(
        ("method", "path", "body", "query", "status"),
        [
            pytest.param("GET", "/api/autoscan/github/status", None,
                         {"project_name": "alpha"}, 200, id="status"),
            pytest.param("GET", "/api/autoscan/github/status", None,
                         {"project_name": "ghost"}, 404, id="status-unknown-workspace"),
            pytest.param("GET", "/api/autoscan/github/labels", None,
                         {"project_name": "alpha"}, 400, id="labels-without-gh"),
            pytest.param("POST", "/api/autoscan/github/setup",
                         {"project_name": "alpha", "action": "install"}, None, 400,
                         id="install-without-installer"),
            pytest.param("POST", "/api/autoscan/github/setup",
                         {"project_name": "alpha", "action": "sign_in"}, None, 400,
                         id="sign-in-without-gh"),
            pytest.param("POST", "/api/autoscan/github/setup",
                         {"project_name": "ghost", "action": "install"}, None, 404,
                         id="setup-unknown-workspace"),
            pytest.param("POST", "/api/autoscan/start",
                         {"project_name": "alpha", "source": "github", "cli": "claude"}, None, 400,
                         id="start-without-github-labels"),
        ],
    )
    async def test_both_transports_answer_identically(
        self, client, auth_headers, relay, no_gh, workspace, method, path, body, query, status,
    ):
        resp = await client.request(method, path, headers=auth_headers, params=query, json=body)
        dispatched = await relay._dispatch_proxy(method, path, body, query, AsyncMock())

        assert resp.status_code == status
        assert dispatched == {"status": status, "body": resp.json()}
        if status == 400:
            assert resp.json()["detail"]

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"project_name": "alpha", "source": "github", "cli": "aider"},
                         id="unknown-agent"),
            pytest.param({"project_name": "alpha", "source": "jira"}, id="unknown-source"),
            pytest.param({"project_name": "alpha", "source": "github",
                          "github_filter": {"labels": ["a,b"]}}, id="comma-in-label"),
        ],
    )
    async def test_invalid_start_is_422_on_both_transports(
        self, client, auth_headers, relay, workspace, body,
    ):
        resp = await client.post("/api/autoscan/start", headers=auth_headers, json=body)
        dispatched = await relay._dispatch_proxy(
            "POST", "/api/autoscan/start", body, None, AsyncMock(),
        )

        assert resp.status_code == dispatched["status"] == 422

    async def test_setup_in_a_deleted_workspace_folder_is_400_on_both_transports(
        self, app, client, auth_headers, relay, workspace, mock_app_state, monkeypatch,
    ):
        monkeypatch.setattr(
            service, "install_command",
            lambda: SetupCommand(argv=[sys.executable, "-c", "pass"], display="install gh"),
        )
        mock_app_state.session_manager = app.state.session_manager
        folder = app.state.workspaces_store.get(workspace).path
        os.rmdir(folder)
        body = {"project_name": workspace, "action": "install"}

        resp = await client.post("/api/autoscan/github/setup", headers=auth_headers, json=body)
        relayed = await relay._proxy_response(
            {"request_id": "r-1", "method": "POST", "path": "/api/autoscan/github/setup",
             "body": body},
            AsyncMock(),
        )

        # A 500 would hide which folder is gone.
        assert resp.status_code == relayed["status"] == 400
        assert folder in resp.json()["detail"]
        assert folder in relayed["body"]["detail"]

    async def test_both_version_builders_advertise_the_fixture_capability(
        self, client, auth_headers, relay,
    ):
        expected = _case("VersionInfoResponse", "autoscan_capability")["wire"]["capabilities"]["autoscan"]

        direct = await client.get("/api/version", headers=auth_headers)
        relayed = await relay._dispatch_proxy("GET", "/api/version", None, None, AsyncMock())

        assert AUTOSCAN_CAPABILITIES == expected
        assert direct.json()["capabilities"]["autoscan"] == expected
        assert relayed["body"]["capabilities"]["autoscan"] == expected


class _PinnedGh(GhCli):
    def resolve_binary(self) -> str:
        return "/opt/gh/bin/gh"


class _RecordingSessions:
    def __init__(self) -> None:
        self.configs: list = []

    async def create_session(self, config):
        self.configs.append(config)
        return SimpleNamespace(to_info=lambda: config.session_id)


@requires_git
@pytest.mark.parametrize(
    ("remotes", "host"),
    [
        pytest.param([("origin", "https://github.acme.dev/acme/app.git")], "github.acme.dev",
                     id="enterprise-origin"),
        pytest.param(
            [("origin", "git@gitlab.com:acme/app.git"),
             ("upstream", "https://github.com/acme/app.git")],
            "github.com", id="github-remote-over-a-gitlab-origin",
        ),
    ],
)
async def test_sign_in_opens_an_interactive_login_for_the_repository_host(
    tmp_path, monkeypatch, remotes, host,
):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.delenv("GH_PROMPT_DISABLED", raising=False)
    repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
    for name, url in remotes:
        run_git_sync(repo, "remote", "add", name, url)
    store = WorkspacesStore(str(tmp_path / "projects.json"))
    store.load()
    store.create(WorkspaceRecord(name="alpha", path=repo))
    sessions = _RecordingSessions()
    state = SimpleNamespace(
        workspaces_store=store,
        autoscan_manager=SimpleNamespace(gh=_PinnedGh()),
        session_manager=sessions,
        broadcaster=None,
    )

    await service.start_github_setup(state, "alpha", "sign_in", 30, 100)

    [config] = sessions.configs
    assert config.command == [
        "/opt/gh/bin/gh", "auth", "login", "--hostname", host,
        "--git-protocol", "https",
    ]
    assert config.kind is SessionKind.SHELL
    assert config.exits_when_done is True
    assert (config.project_name, config.rows, config.cols) == ("alpha", 30, 100)
    # The sign-in asks how to sign in: the session must not turn prompts off.
    assert "GH_PROMPT_DISABLED" not in config.env


class _EnterpriseUpstreamGh(_PinnedGh):
    """``gh repo view`` reads an ``upstream`` on GitHub Enterprise; only
    github.com is signed in."""

    async def version(self) -> str | None:
        return "2.83.0"

    async def auth_status(self, host: str) -> GhAuth:
        if host == "github.com":
            return GhAuth(signed_in=True, account="octocat")
        return GhAuth(signed_in=False, detail=f"not signed in to {host}")

    async def repo_of(self, path: str) -> GitHubRepo:
        return GitHubRepo(
            host="github.acme.dev", name_with_owner="acme/app",
            url="https://github.acme.dev/acme/app",
        )


@requires_git
async def test_sign_in_goes_to_the_host_of_the_repository_gh_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    repo = init_repo(str(tmp_path / "repo"), initial_commit=False)
    run_git_sync(repo, "remote", "add", "origin", "https://github.com/acme/app.git")
    run_git_sync(repo, "remote", "add", "upstream", "https://github.acme.dev/acme/app.git")
    store = WorkspacesStore(str(tmp_path / "projects.json"))
    store.load()
    store.create(WorkspaceRecord(name="alpha", path=repo))
    sessions = _RecordingSessions()
    state = SimpleNamespace(
        workspaces_store=store,
        autoscan_manager=SimpleNamespace(gh=_EnterpriseUpstreamGh()),
        session_manager=sessions,
        broadcaster=None,
    )

    await service.start_github_setup(state, "alpha", "sign_in", 30, 100)

    # Signing in to the first GitHub remote (github.com) again would leave
    # the scan blocked on the host it really reads.
    [config] = sessions.configs
    assert config.command == [
        "/opt/gh/bin/gh", "auth", "login", "--hostname", "github.acme.dev",
        "--git-protocol", "https",
    ]


@pytest.mark.parametrize(
    "case_name", ["github_source_other_agent", "linear_source_claude"],
)
def test_start_request_fixture_round_trips_through_the_agent_model(case_name):
    wire = _case("StartScanRequest", case_name)["wire"]

    parsed = StartScanRequest.model_validate(wire)

    assert parsed.model_dump(mode="json", exclude_unset=True) == wire
    start = parsed.to_start()
    assert (start.source, start.cli) == (wire["source"], wire["cli"])
    assert (start.github_filter is None) is ("github_filter" not in wire)
    assert (start.linear_filter is None) is ("linear_filter" not in wire)


@pytest.mark.parametrize(
    ("type_name", "model", "case_name"),
    [
        pytest.param("AutoScanInstanceDto", AutoScanInstanceDto, "github_source_other_agent",
                     id="instance"),
        pytest.param("ProjectInfo", WorkspaceReadModel, "auto_scan_choices", id="workspace-chosen"),
        pytest.param("ProjectInfo", WorkspaceReadModel, "auto_scan_nothing_chosen",
                     id="workspace-unchosen"),
    ],
)
def test_agent_output_fixture_matches_the_agent_model(type_name, model, case_name):
    wire = _case(type_name, case_name)["wire"]

    assert model.model_validate(wire).model_dump(mode="json") == wire
