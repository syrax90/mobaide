"""Auto Scan's GitHub issue types: how ``GhCli`` reads them, how
``GitHubIssueSource`` keeps only issues of the chosen types, what the labels
route answers over both transports, and how the filter models and the
workspaces store clean the type names.

``gh`` never runs: ``subprocess.run`` is swapped for a scripted answer, or
``GhCli`` for a hand-written fake.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mobaide.agent.workspaces.models import GitHubScanFilterDraftModel, GitHubScanFilterModel
from mobaide.core import WorkspaceRecord, WorkspacesStore
from mobaide.core.workspaces_config import GitHubScanFilter
from mobaide.github import gh as gh_module
from mobaide.github.gh import GhCli, GhError, GhIssue, GitHubRepo
from mobaide.linear import sources
from mobaide.linear.sources import GitHubIssueSource
from mobaide.relay.client import RelayClient

_REPO = GitHubRepo(host="ghe.example.com", name_with_owner="acme/app",
                   url="https://ghe.example.com/acme/app")


class _PinnedGh(GhCli):
    def resolve_binary(self) -> str:
        return "gh"


class _ScriptedRun:
    """Stands in for ``subprocess.run``: answers each call with the next
    ``(returncode, stdout, stderr)`` and records the ``gh`` arguments and
    timeouts."""

    def __init__(self, answers: list[tuple[int, str, str]]) -> None:
        self._answers = list(answers)
        self.args: list[list[str]] = []
        self.timeouts: list[float | None] = []

    def __call__(self, argv, **kwargs):
        self.args.append(list(argv[1:]))
        self.timeouts.append(kwargs.get("timeout"))
        rc, out, err = self._answers.pop(0)
        return subprocess.CompletedProcess(argv, rc, stdout=out.encode(), stderr=err.encode())


def _json_fields(args: list[str]) -> list[str]:
    return args[args.index("--json") + 1].split(",")


_OLD_GH_HINT = r"cannot filter by issue type\. Update gh on the PC, or clear the issue types\."


class TestGhCli:
    async def test_issue_list_filters_and_reads_the_type_only_when_one_is_asked(
        self, monkeypatch,
    ):
        rows = [
            {"number": 1, "title": "a", "labels": [], "issueType": {"name": "Bug"}},
            {"number": 2, "title": "b", "labels": [], "issueType": None},
        ]
        run = _ScriptedRun([(0, json.dumps(rows), ""), (0, "[]", "")])
        monkeypatch.setattr(gh_module.subprocess, "run", run)
        gh = _PinnedGh()

        typed = await gh.list_issues(_REPO, label="ai", assignee=None, issue_type="Bug")
        await gh.list_issues(_REPO, label="ai", assignee=None)

        assert [(i.number, i.issue_type) for i in typed] == [(1, "Bug"), (2, None)]
        assert "issueType" in _json_fields(run.args[0])
        assert "--type=Bug" in run.args[0]
        # A gh too old to know the field or the flag still lists issues when
        # no type is chosen.
        assert "issueType" not in _json_fields(run.args[1])
        assert not any(arg.startswith("--type") for arg in run.args[1])

    @pytest.mark.parametrize(
        ("issue_type", "with_types", "limit", "reads_types", "timeout"),
        [
            # Above 10 (label, type) pairs: issues of other types are never
            # taken, so they stay labelled and must not fill the answer. gh
            # pages through those rows, so the call gets more time.
            pytest.param(None, True, "300", True, 60.0, id="label-only-fallback"),
            pytest.param("Bug", True, "50", True, 20.0, id="per-pair-type"),
            pytest.param(None, False, "50", False, 20.0, id="untyped"),
        ],
    )
    async def test_only_the_label_only_typed_fallback_reads_past_the_50_issue_window(
        self, monkeypatch, issue_type, with_types, limit, reads_types, timeout,
    ):
        run = _ScriptedRun([(0, "[]", "")])
        monkeypatch.setattr(gh_module.subprocess, "run", run)

        await _PinnedGh().list_issues(
            _REPO, label="ai", assignee=None, issue_type=issue_type, with_types=with_types,
        )

        [args] = run.args
        assert args[args.index("--limit") + 1] == limit
        assert ("issueType" in _json_fields(args)) is reads_types
        assert run.timeouts == [timeout]

    @pytest.mark.parametrize(
        "stderr",
        [
            pytest.param('Unknown JSON field: "issueType"\nAvailable fields:\n  assignees',
                         id="unknown-field"),
            pytest.param("unknown flag: --type\n\nUsage:  gh issue list [flags]",
                         id="unknown-flag"),
        ],
    )
    async def test_a_gh_without_issue_types_says_what_to_do(self, monkeypatch, stderr):
        monkeypatch.setattr(gh_module.subprocess, "run", _ScriptedRun([(1, "", stderr)]))

        with pytest.raises(GhError, match=_OLD_GH_HINT):
            await _PinnedGh().list_issues(_REPO, label="ai", assignee=None, issue_type="Bug")

    @pytest.mark.parametrize(
        ("issue_type", "stderr"),
        [
            pytest.param("Bug", "HTTP 401: Bad credentials", id="type-asked-other-error"),
            # Without a type the hint would point at the wrong fix.
            pytest.param(None, "unknown flag: --type", id="no-type-asked"),
        ],
    )
    async def test_other_issue_list_failures_keep_gh_own_error(
        self, monkeypatch, issue_type, stderr,
    ):
        monkeypatch.setattr(gh_module.subprocess, "run", _ScriptedRun([(1, "", stderr)]))

        with pytest.raises(GhError) as caught:
            await _PinnedGh().list_issues(
                _REPO, label="ai", assignee=None, issue_type=issue_type,
            )

        assert str(caught.value) == stderr.strip()

    async def test_issue_types_lists_the_enabled_types_of_the_repository_host(self, monkeypatch):
        nodes = [
            {"name": "Bug", "color": "RED", "description": "Something is broken",
             "isEnabled": True},
            {"name": "Epic", "color": "PURPLE", "description": "", "isEnabled": False},
            {"name": "Task", "color": None, "description": None},
        ]
        answer = {"data": {"repository": {"issueTypes": {"nodes": nodes}}}}
        run = _ScriptedRun([(0, json.dumps(answer), "")])
        monkeypatch.setattr(gh_module.subprocess, "run", run)

        types = await _PinnedGh().list_issue_types(_REPO)

        assert types == [
            {"name": "Bug", "color": "RED", "description": "Something is broken"},
            {"name": "Task", "color": "", "description": ""},
        ]
        [args] = run.args
        assert args[:4] == ["api", "graphql", "--hostname", "ghe.example.com"]
        assert "owner=acme" in args and "name=app" in args

    @pytest.mark.parametrize(
        "answer",
        [pytest.param('{"data": {"repository": null}}', id="no-repository"),
         pytest.param("not json", id="not-json")],
    )
    async def test_an_unexpected_issue_type_answer_is_a_gh_error(self, monkeypatch, answer):
        monkeypatch.setattr(gh_module.subprocess, "run", _ScriptedRun([(0, answer, "")]))

        with pytest.raises(GhError):
            await _PinnedGh().list_issue_types(_REPO)


class _TypedRepository:
    """Open issues that carry every asked label, filtered by type the way
    GitHub's search does: against its index (``indexed``, case ignored),
    while each answer carries the issue's current type, read only when a
    type or ``with_types`` is asked. Records each ``(label, issue_type)``
    query."""

    def __init__(
        self, types: dict[int, str | None], *, indexed: dict[int, str | None] | None = None,
    ) -> None:
        self.types = types
        self.indexed = {**types, **(indexed or {})}
        self.queries: list[tuple[str, str | None]] = []

    async def list_issues(self, repo, *, label, assignee, issue_type=None, with_types=False):
        self.queries.append((label, issue_type))
        return [
            GhIssue(number=number, title=f"Issue {number}", body="", url="", labels=(label,),
                    issue_type=current if issue_type or with_types else None)
            for number, current in self.types.items()
            if issue_type is None
            or (self.indexed[number] or "").casefold() == issue_type.casefold()
        ]


@pytest.fixture
def _github_ready(monkeypatch):
    async def ready(_gh, _path):
        return {"problem": None, "host": "github.com", "repo": "acme/app",
                "repo_url": "https://github.com/acme/app"}

    monkeypatch.setattr(sources, "github_status", ready)


@pytest.mark.usefixtures("_github_ready")
@pytest.mark.parametrize(
    ("issue_types", "taken", "queries"),
    [
        # Names match without regard to case; an issue with no type is left
        # out. An issue found by two queries is taken once.
        pytest.param(
            ["bug", "Feature"], ["1", "4"],
            [("ai", "bug"), ("ai", "Feature"), ("bot", "bug"), ("bot", "Feature")],
            id="chosen-types",
        ),
        pytest.param([], ["1", "2", "3", "4"], [("ai", None), ("bot", None)], id="any-type"),
    ],
)
async def test_fetch_asks_github_for_each_label_and_type(issue_types, taken, queries):
    repo = _TypedRepository({1: "Bug", 2: "Task", 3: None, 4: "FEATURE"})
    source = GitHubIssueSource(
        cast("GhCli", repo), "/srv/app",
        GitHubScanFilter(labels=["ai", "bot"], issue_types=issue_types),
    )
    await source.connect()

    issues = await source.fetch()

    assert [i.key for i in issues] == taken
    assert repo.queries == queries


_SIX_TYPES = ["bug", "Feature", "Task", "Epic", "Spike", "Chore"]


@pytest.mark.usefixtures("_github_ready")
@pytest.mark.parametrize(
    ("issue_types", "queries"),
    [
        # 2 labels x 5 types = 10 searches: still one per pair.
        pytest.param(
            _SIX_TYPES[:5],
            [(label, t) for label in ("ai", "bot") for t in _SIX_TYPES[:5]],
            id="ten-pairs-ask-per-pair",
        ),
        # 2 labels x 6 types = 12 searches: one per label, types read and
        # filtered by the agent.
        pytest.param(_SIX_TYPES, [("ai", None), ("bot", None)], id="twelve-pairs-ask-per-label"),
    ],
)
async def test_fetch_past_ten_label_type_pairs_asks_once_per_label_and_filters_types_itself(
    issue_types, queries,
):
    repo = _TypedRepository({1: "Bug", 2: "Wiki", 3: None, 4: "FEATURE"})
    source = GitHubIssueSource(
        cast("GhCli", repo), "/srv/app",
        GitHubScanFilter(labels=["ai", "bot"], issue_types=issue_types),
    )
    await source.connect()

    issues = await source.fetch()

    assert [i.key for i in issues] == ["1", "4"]
    assert repo.queries == queries


@pytest.mark.usefixtures("_github_ready")
async def test_fetch_leaves_out_an_issue_whose_type_changed_after_github_indexed_it():
    # Issue 2 was a Bug when GitHub's search indexed it; it is a Task now.
    repo = _TypedRepository({1: "Bug", 2: "Task"}, indexed={2: "Bug"})
    source = GitHubIssueSource(
        cast("GhCli", repo), "/srv/app", GitHubScanFilter(labels=["ai"], issue_types=["Bug"]),
    )
    await source.connect()

    issues = await source.fetch()

    assert [i.key for i in issues] == ["1"]


class _LabelsGh(GhCli):
    def __init__(self, *, types_error: str | None = None) -> None:
        super().__init__()
        self._types_error = types_error

    def resolve_binary(self) -> str:
        return "gh"

    async def repo_of(self, path: str) -> GitHubRepo:
        return _REPO

    async def list_labels(self, repo: GitHubRepo) -> list[dict[str, str]]:
        return [{"name": "auto-scan", "color": "0e8a16"}]

    async def list_issue_types(self, repo: GitHubRepo) -> list[dict[str, str]]:
        if self._types_error is not None:
            raise GhError(self._types_error)
        return [{"name": "Bug", "color": "RED", "description": "Something is broken"}]


@pytest.mark.parametrize(
    ("gh", "issue_types"),
    [
        pytest.param(_LabelsGh(), [{"name": "Bug", "color": "RED",
                                    "description": "Something is broken"}], id="with-types"),
        # An older GitHub Enterprise Server has no issue types: labels still come.
        pytest.param(_LabelsGh(types_error="Field 'issueTypes' doesn't exist"), [],
                     id="types-unavailable"),
    ],
)
async def test_labels_route_answers_issue_types_on_both_transports(
    app, client, auth_headers, tmp_path, monkeypatch, relay_config, mock_session_manager,
    mock_event_bus, mock_broadcaster, mock_app_state, gh, issue_types,
):
    folder = tmp_path / "alpha"
    folder.mkdir()
    app.state.workspaces_store.create(WorkspaceRecord(name="alpha", path=str(folder)))
    manager = SimpleNamespace(gh=gh)
    monkeypatch.setattr(app.state, "autoscan_manager", manager)
    mock_app_state.workspaces_store = app.state.workspaces_store
    mock_app_state.autoscan_manager = manager
    relay = RelayClient(
        config=relay_config, session_manager=mock_session_manager, event_bus=mock_event_bus,
        broadcaster=mock_broadcaster, app_state=mock_app_state,
    )
    path, query = "/api/autoscan/github/labels", {"project_name": "alpha"}

    resp = await client.get(path, headers=auth_headers, params=query)
    relayed = await relay._dispatch_proxy("GET", path, None, query, AsyncMock())

    assert resp.status_code == 200
    assert resp.json() == {
        "labels": [{"name": "auto-scan", "color": "0e8a16"}], "issue_types": issue_types,
    }
    assert relayed == {"status": 200, "body": resp.json()}


@pytest.mark.parametrize("model", [GitHubScanFilterModel, GitHubScanFilterDraftModel])
class TestFilterModelIssueTypes:
    def test_issue_types_are_trimmed_and_distinct_ignoring_case(self, model):
        f = model(labels=["ai"], issue_types=[" Bug ", "bug", "Task"])

        assert f.issue_types == ["Bug", "Task"]

    @pytest.mark.parametrize(
        "issue_types",
        [pytest.param(["  "], id="blank"), pytest.param(["x" * 101], id="long"),
         pytest.param([f"t{i}" for i in range(21)], id="21-types")],
    )
    def test_rejects(self, model, issue_types):
        with pytest.raises(ValidationError):
            model(labels=["ai"], issue_types=issue_types)


def test_only_a_template_filter_may_still_lack_labels():
    # A start needs a label to scan for; a template may leave it to the wizard.
    assert GitHubScanFilterDraftModel(labels=[], issue_types=["Bug"]).labels == []
    with pytest.raises(ValidationError):
        GitHubScanFilterModel(labels=[], issue_types=["Bug"])


def test_store_reads_back_distinct_issue_types_and_drops_unusable_ones(tmp_path):
    path = tmp_path / "projects.json"
    path.write_text(json.dumps([{
        "name": "alpha", "path": str(tmp_path),
        "github_scan_filter": {"labels": ["ai"], "issue_types": ["Bug", " BUG ", "", 3, "Task"]},
    }]), encoding="utf-8")
    store = WorkspacesStore(str(path))
    store.load()

    snap = store.get("alpha")

    assert snap is not None and snap.github_scan_filter is not None
    assert snap.github_scan_filter.issue_types == ["Bug", "Task"]
    [row] = store.list_public()
    assert row["auto_scan"]["github_filter"]["issue_types"] == ["Bug", "Task"]  # type: ignore[index]
