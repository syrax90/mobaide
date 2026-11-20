"""Auto Scan's GitHub issue source (``mobaide.linear.sources.GitHubIssueSource``)
over an in-memory repository that stands in for ``gh``.

The source must take each matching issue once: it finds open issues by any
trigger label, takes one by swapping its trigger labels for the active label,
and skips an issue it could not take until the scan restarts, so a refused
label removal can never start a new session on every cycle.
"""
from __future__ import annotations

from typing import cast

import pytest

from mobaide.core.workspaces_config import GitHubScanFilter
from mobaide.github.gh import GhCli, GhError, GhIssue
from mobaide.linear import sources
from mobaide.linear.sources import AutoScanSourceError, GitHubIssueSource, ScanIssue
from mobaide.linear.workitems import ActiveWorkItem, now_iso_utc

_READY = {
    "installed": True, "version": "2.83.0", "signed_in": True, "account": "octocat",
    "host": "github.com", "repo": "acme/app", "repo_url": "https://github.com/acme/app",
    "problem": None, "can_install": False, "install_command": None,
}


class _FakeRepository:
    """Open issues and their labels, edited the way ``gh issue edit`` would."""

    def __init__(self, labels: dict[int, set[str]], *, refuse_removal: set[int] | None = None):
        self.labels = labels
        self.closed: set[int] = set()
        self.refuse_removal = refuse_removal or set()

    async def list_issues(self, repo, *, label, assignee, issue_type=None, with_types=False):
        return [
            GhIssue(
                number=number, title=f"Issue {number}", body="", labels=tuple(sorted(names)),
                url=f"https://github.com/acme/app/issues/{number}",
            )
            for number, names in self.labels.items()
            if number not in self.closed and label in names
        ]

    async def edit_labels(self, repo, number, *, add=(), remove=()):
        if remove and number in self.refuse_removal:
            raise GhError("HTTP 403: Resource not accessible by integration")
        self.labels[number] = (self.labels[number] | set(add)) - set(remove)

    async def issue_state(self, repo, number):
        if number not in self.labels:
            return None
        return "CLOSED" if number in self.closed else "OPEN"


class _Steps:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []

    def __call__(self, issue: ScanIssue, step: str, status: str, error: str | None) -> None:
        self.seen.append((issue.identifier, step, status))


@pytest.fixture(autouse=True)
def _github_ready(monkeypatch):
    async def ready(_gh, _path):
        return dict(_READY)

    monkeypatch.setattr(sources, "github_status", ready)


async def _connected(
    repo: _FakeRepository, *, labels: list[str], active: str | None = "in-progress",
) -> GitHubIssueSource:
    source = GitHubIssueSource(
        cast("GhCli", repo), "/srv/app", GitHubScanFilter(labels=labels, active_label=active),
    )
    await source.connect()
    return source


def _item(number: str, repo: str = "") -> ActiveWorkItem:
    return ActiveWorkItem(
        id=f"#{number}", uuid=number, slug=f"gh-{number}", worktree_path=f"/wt/{number}",
        project_root="/srv/app", added_at=now_iso_utc(), source="github", repo=repo,
    )


async def test_fetch_finds_each_issue_once_across_trigger_labels():
    repo = _FakeRepository({3: {"ai", "bot"}, 1: {"ai"}, 2: {"bug"}, 5: {"bot"}})
    source = await _connected(repo, labels=["ai", "bot"])

    issues = await source.fetch()

    assert [(i.key, i.identifier, i.slug_seed) for i in issues] == [
        ("1", "#1", "gh-1"), ("3", "#3", "gh-3"), ("5", "#5", "gh-5"),
    ]
    assert issues[1].url == "https://github.com/acme/app/issues/3"


async def test_taken_issue_swaps_trigger_labels_for_the_active_label():
    repo = _FakeRepository({3: {"ai", "bot", "bug"}, 4: {"ai"}})
    source = await _connected(repo, labels=["ai", "bot"])
    steps = _Steps()
    issue = next(i for i in await source.fetch() if i.key == "3")

    await source.claim(issue, steps)

    assert repo.labels[3] == {"bug", "in-progress"}
    assert ("#3", "label_removal", "ok") in steps.seen
    assert [i.key for i in await source.fetch()] == ["4"]


async def test_an_active_label_that_is_also_a_trigger_label_is_not_added():
    # A filter saved before the agent refused this pair.
    repo = _FakeRepository({8: {"ai"}})
    source = await _connected(repo, labels=["ai", "bot"], active="Bot")
    steps = _Steps()
    [issue] = await source.fetch()

    await source.claim(issue, steps)

    # Adding "Bot" would make the issue match again on the next cycle.
    assert repo.labels[8] == set()
    assert ("#8", "state_update", "ok") in steps.seen


async def test_refused_label_removal_skips_the_issue_until_the_scan_restarts():
    repo = _FakeRepository({1: {"ai"}, 2: {"ai"}}, refuse_removal={1})
    source = await _connected(repo, labels=["ai"])
    steps = _Steps()
    first = next(i for i in await source.fetch() if i.key == "1")

    await source.claim(first, steps)

    assert ("#1", "label_removal", "failed") in steps.seen
    assert "ai" in repo.labels[1]  # GitHub kept the trigger label...
    assert [i.key for i in await source.fetch()] == ["2"]  # ...but this scan skips it.
    restarted = await _connected(repo, labels=["ai"])
    assert [i.key for i in await restarted.fetch()] == ["1", "2"]


async def test_an_issue_is_done_once_it_is_closed():
    repo = _FakeRepository({7: set()})
    source = await _connected(repo, labels=["ai"])

    assert await source.is_done(_item("7")) is False
    repo.closed.add(7)
    assert await source.is_done(_item("7")) is True
    assert await source.is_done(_item("99")) is None
    assert await source.is_done(_item("TEAM-1")) is None


async def test_an_issue_of_another_repository_is_never_judged_done():
    repo = _FakeRepository({7: set()})
    repo.closed.add(7)
    source = await _connected(repo, labels=["ai"])

    assert await source.is_done(_item("7", repo="acme/app")) is True
    assert await source.is_done(_item("7", repo="acme/other")) is None


async def test_connect_refuses_a_workspace_github_cannot_scan(monkeypatch):
    async def blocked(_gh, _path):
        return {**_READY, "signed_in": False, "repo": None, "problem": "Sign in to GitHub."}

    monkeypatch.setattr(sources, "github_status", blocked)
    source = GitHubIssueSource(
        cast("GhCli", _FakeRepository({})), "/srv/app", GitHubScanFilter(labels=["ai"]),
    )

    with pytest.raises(AutoScanSourceError, match="Sign in to GitHub."):
        await source.connect()


class _SearchIndexRepository(_FakeRepository):
    """GitHub's issue search: it matches a label in any case, and its index
    still finds an issue by the labels it had a moment ago, while each answer
    carries the issue's current labels. ``gh issue edit`` removes only the
    exact label name it is given."""

    def __init__(self, labels: dict[int, set[str]]):
        super().__init__(labels)
        self.indexed = {number: set(names) for number, names in labels.items()}

    async def list_issues(self, repo, *, label, assignee, issue_type=None, with_types=False):
        return [
            GhIssue(
                number=number, title=f"Issue {number}", body="",
                labels=tuple(sorted(self.labels[number])),
                url=f"https://github.com/acme/app/issues/{number}",
            )
            for number, names in self.indexed.items()
            if label.casefold() in {name.casefold() for name in names}
        ]


async def test_an_issue_taken_moments_ago_is_not_taken_again_while_search_lags():
    repo = _SearchIndexRepository({3: {"ai"}, 4: {"ai"}})
    source = await _connected(repo, labels=["ai"])
    taken = next(i for i in await source.fetch() if i.key == "3")

    await source.claim(taken, _Steps())

    assert [i.key for i in await source.fetch()] == ["4"]


async def test_labels_that_differ_only_in_case_still_match_and_are_swapped():
    repo = _SearchIndexRepository({5: {"AI-Ready", "bug"}, 6: {"ai-ready", "in-progress"}})
    source = await _connected(repo, labels=["ai-ready"], active="In-Progress")
    steps = _Steps()

    for issue in await source.fetch():
        await source.claim(issue, steps)

    # The trigger label goes by the issue's own spelling, or GitHub keeps it.
    assert repo.labels[5] == {"bug", "In-Progress"}
    # The active label already on the issue is not added a second time.
    assert repo.labels[6] == {"in-progress"}
    assert ("#5", "label_removal", "ok") in steps.seen
    assert ("#6", "label_removal", "ok") in steps.seen
