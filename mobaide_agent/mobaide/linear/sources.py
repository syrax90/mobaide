"""Issue sources for Auto Scan: where a scan finds issues and how it takes them.

A source answers four questions for the scanner: which issues match now
(``fetch``), how to mark one as taken so it stops matching (``claim``),
whether a tracked issue is finished so its worktree can go (``is_done``),
and whether the source is usable at all (``connect``). The scanner owns
everything after the claim: the worktree, the agent session, the events.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mobaide.core.workspaces_config import AutoScanFilter, GitHubScanFilter, WorkspaceConfig
from mobaide.github.gh import GhBinaryNotFoundError, GhCli, GhError, GhIssue, GitHubRepo
from mobaide.github.setup import github_status

from .client import LinearApiClient
from .models import LinearIssue
from .workitems import ActiveWorkItem


# Above this many (label, issue type) pairs a cycle asks once per label and
# filters the types itself: each pair is a GitHub search.
_MAX_TYPED_QUERIES = 10


class AutoScanSourceError(Exception):
    """The scan cannot start; the message tells the user what to fix."""


@dataclass(frozen=True)
class ScanIssue:
    key: str  # Tracker key for single-issue queries: Linear UUID, GitHub number
    identifier: str  # Shown to the user: "TEAM-8", "#42"
    title: str
    description: str | None
    url: str | None
    slug_seed: str  # Identifier part of the worktree folder and branch name
    raw: object = None

    @classmethod
    def from_linear(cls, issue: LinearIssue) -> "ScanIssue":
        return cls(
            key=issue.id,
            identifier=issue.identifier,
            title=issue.title,
            description=issue.description,
            url=None,
            slug_seed=issue.identifier,
            raw=issue,
        )


# (issue, step, status, error) — one ``issue`` scan event.
EmitStep = Callable[[ScanIssue, str, str, "str | None"], None]


class IssueSource(Protocol):
    kind: str
    label: str

    async def connect(self) -> str:
        """Check the source can be used; returns who or what it reads, for the log."""
        ...

    def refresh(self, workspace: WorkspaceConfig | None) -> None:
        """Take the workspace's current filter; ``None`` keeps the last one."""
        ...

    def describe(self) -> str:
        """The current filter in one log-friendly line."""
        ...

    async def fetch(self) -> list[ScanIssue]: ...

    async def claim(self, issue: ScanIssue, emit: EmitStep) -> None: ...

    async def is_done(self, item: ActiveWorkItem) -> bool | None:
        """``True`` when finished, ``False`` when not, ``None`` when unknown."""
        ...


class LinearIssueSource:
    """Issues assigned to the Linear API key's user, matched by state and label."""

    kind = "linear"
    label = "Linear"

    def __init__(self, client: LinearApiClient, filter_cfg: AutoScanFilter) -> None:
        self._client = client
        self.filter = filter_cfg

    async def connect(self) -> str:
        try:
            viewer = await self._client.get_viewer()
        except Exception as exc:
            raise AutoScanSourceError(f"Could not sign in to Linear: {exc}") from exc
        return str(viewer.get("name", "unknown"))

    def refresh(self, workspace: WorkspaceConfig | None) -> None:
        if workspace is not None:
            self.filter = workspace.auto_scan_filter

    def describe(self) -> str:
        return (
            f"state_names={list(self.filter.state_names)} "
            f"label_names={list(self.filter.label_names)}"
        )

    async def fetch(self) -> list[ScanIssue]:
        issues = await self._client.get_auto_scan_issues(self.filter)
        return [ScanIssue.from_linear(issue) for issue in issues]

    async def claim(self, issue: ScanIssue, emit: EmitStep) -> None:
        raw = issue.raw
        assert isinstance(raw, LinearIssue)
        target_state_name = self.filter.active_state
        target_state = next((s for s in raw.team_states if s.name == target_state_name), None)
        emit(issue, "state_update", "started", None)
        if target_state is None:
            emit(
                issue, "state_update", "failed",
                f"workflow state '{target_state_name}' not found",
            )
        else:
            try:
                ok = await self._client.update_issue_state(raw.id, target_state.id)
                emit(
                    issue, "state_update", "ok" if ok else "failed",
                    None if ok else "Linear API returned failure",
                )
            except Exception as exc:
                emit(issue, "state_update", "failed", str(exc))

        removal_name = self.filter.removal_label
        auto_label = next((l for l in raw.labels if l.name == removal_name), None)
        emit(issue, "label_removal", "started", None)
        if auto_label is None:
            emit(
                issue, "label_removal", "ok",
                f"label '{removal_name}' not present on issue",
            )
        else:
            try:
                await self._client.remove_issue_label(raw.id, auto_label.id)
                emit(issue, "label_removal", "ok", None)
            except Exception as exc:
                emit(issue, "label_removal", "failed", str(exc))

    async def is_done(self, item: ActiveWorkItem) -> bool | None:
        state = await self._client.get_issue_state(item.uuid)
        if state is None:
            return None
        return state.name == self.filter.done_state


class GitHubIssueSource:
    """Open issues of the workspace's GitHub repository, matched by label and,
    when the filter names issue types, by type.

    Taking an issue removes its trigger labels (and adds ``active_label``),
    so it stops matching. When GitHub refuses the removal, the issue would
    match again on every cycle; it is then skipped until the scan restarts.
    """

    kind = "github"
    label = "GitHub"

    def __init__(self, gh: GhCli, workspace_path: str, filter_cfg: GitHubScanFilter) -> None:
        self._gh = gh
        self._path = workspace_path
        self.filter = filter_cfg
        self._repo: GitHubRepo | None = None
        self._stuck: set[int] = set()

    @property
    def repo(self) -> GitHubRepo | None:
        return self._repo

    async def connect(self) -> str:
        status = await github_status(self._gh, self._path)
        problem = status.get("problem")
        if problem:
            raise AutoScanSourceError(str(problem))
        repo = GitHubRepo(
            host=str(status["host"]),
            name_with_owner=str(status["repo"]),
            url=str(status["repo_url"]),
        )
        self._repo = repo
        return f"{status.get('account') or 'unknown'} on {repo.name_with_owner}"

    def refresh(self, workspace: WorkspaceConfig | None) -> None:
        if workspace is not None and workspace.github_scan_filter is not None:
            self.filter = workspace.github_scan_filter

    def describe(self) -> str:
        repo = self._repo.name_with_owner if self._repo else "?"
        types = list(self.filter.issue_types) or "*"
        return (
            f"repo={repo} labels={list(self.filter.labels)} "
            f"assignee={self.filter.assignee or '*'} types={types}"
        )

    def tool_dir(self) -> str | None:
        """The folder of the ``gh`` this scan uses, for the agent sessions'
        PATH: the first prompt asks the agent to run ``gh issue view``, and
        ``gh`` may have been found outside the agent's PATH."""
        try:
            return os.path.dirname(self._gh.resolve_binary())
        except GhBinaryNotFoundError:
            return None

    def _triggers(self) -> set[str]:
        # GitHub matches label names without regard to case.
        return {label.casefold() for label in self.filter.labels}

    def _require_repo(self) -> GitHubRepo:
        if self._repo is None:
            raise GhError("the GitHub repository is not known yet")
        return self._repo

    async def fetch(self) -> list[ScanIssue]:
        repo = self._require_repo()
        triggers = self._triggers()
        types = {name.casefold() for name in self.filter.issue_types}
        found: dict[int, GhIssue] = {}
        labels = list(self.filter.labels)
        typed = 0 < len(labels) * len(self.filter.issue_types) <= _MAX_TYPED_QUERIES
        queries = [
            (label, issue_type)
            for label in labels
            for issue_type in (self.filter.issue_types if typed else [None])
        ]
        for label, issue_type in queries:
            for issue in await self._gh.list_issues(
                repo, label=label, assignee=self.filter.assignee,
                issue_type=issue_type, with_types=bool(types),
            ):
                # GitHub's search matched the type already; its index can lag
                # behind a type change, and the answer's type is current.
                if types and (issue.issue_type or "").casefold() not in types:
                    continue
                # The label search can lag behind a claim; the labels in the
                # answer are current, so an issue already taken is left out.
                if any(name.casefold() in triggers for name in issue.labels):
                    found.setdefault(issue.number, issue)
        return [
            ScanIssue(
                key=str(issue.number),
                identifier=f"#{issue.number}",
                title=issue.title,
                description=issue.body or None,
                url=issue.url or None,
                slug_seed=f"gh-{issue.number}",
                raw=issue,
            )
            for number, issue in sorted(found.items())
            if number not in self._stuck
        ]

    async def claim(self, issue: ScanIssue, emit: EmitStep) -> None:
        raw = issue.raw
        assert isinstance(raw, GhIssue)
        repo = self._require_repo()
        active = self.filter.active_label
        emit(issue, "state_update", "started", None)
        if not active:
            emit(issue, "state_update", "ok", "no label to add")
        elif active.casefold() in self._triggers():
            # It would make the issue match again on the next cycle.
            emit(issue, "state_update", "ok", f"label '{active}' is a trigger label; not added")
        elif active.casefold() in {name.casefold() for name in raw.labels}:
            emit(issue, "state_update", "ok", f"label '{active}' already on issue")
        else:
            try:
                await self._gh.edit_labels(repo, raw.number, add=(active,))
                emit(issue, "state_update", "ok", None)
            except GhError as exc:
                emit(issue, "state_update", "failed", str(exc))

        triggers = self._triggers()
        # The issue's own spelling: that is the label GitHub removes.
        remove = tuple(name for name in raw.labels if name.casefold() in triggers)
        emit(issue, "label_removal", "started", None)
        if not remove:
            emit(issue, "label_removal", "ok", "no trigger label on issue")
            return
        try:
            await self._gh.edit_labels(repo, raw.number, remove=remove)
            emit(issue, "label_removal", "ok", None)
        except GhError as exc:
            self._stuck.add(raw.number)
            emit(
                issue, "label_removal", "failed",
                f"{exc}; this scan will not take the issue again",
            )

    async def is_done(self, item: ActiveWorkItem) -> bool | None:
        repo = self._require_repo()
        try:
            number = int(item.uuid)
        except ValueError:
            return None
        state = await self._gh.issue_state(repo, number)
        if state is None:
            return None
        return state.upper() == "CLOSED"
